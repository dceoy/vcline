#!/usr/bin/env python

import re
from pathlib import Path

import luigi
from luigi.util import requires

from .align import PrepareCRAMNormal
from .base import ShellTask
from .ref import (FetchDbsnpVCF, FetchEvaluationIntervalList, FetchHapmapVCF,
                  FetchMillsIndelVCF, FetchReferenceFASTA)
from .samtools import MergeSAMsIntoSortedSAM


@requires(FetchEvaluationIntervalList, FetchReferenceFASTA)
class SplitEvaluationIntervals(ShellTask):
    cf = luigi.DictParameter()
    priority = 40

    def output(self):
        return [
            luigi.LocalTarget(
                str(
                    Path(self.cf['germline_snv_indel_gatk_dir_path']).joinpath(
                        f'{i:04d}-scattered.interval_list'
                    )
                )
            ) for i in range(self.cf['n_cpu_per_worker'])
        ]

    def run(self):
        interval_path = self.input()[0].path
        run_id = Path(interval_path).stem
        self.print_log(f'Split an evaluation interval list:\t{run_id}')
        gatk = self.cf['gatk']
        gatk_opts = ' --java-options "{}"'.format(self.cf['gatk_java_options'])
        fa_path = self.input()[1][0].path
        scatter_count = self.cf['n_cpu_per_worker']
        self.setup_shell(
            run_id=run_id, log_dir_path=self.cf['log_dir_path'], commands=gatk,
            cwd=self.cf['germline_snv_indel_gatk_dir_path'],
            remove_if_failed=self.cf['remove_if_failed']
        )
        self.run_shell(
            args=(
                'set -e && '
                + f'{gatk}{gatk_opts} SplitIntervals'
                + f' --reference {fa_path}'
                + f' --intervals {interval_path}'
                + f' --scatter-count {scatter_count}'
                + ' --output'
                + ' {}'.format(self.cf['germline_snv_indel_gatk_dir_path'])
            ),
            input_files_or_dirs=[interval_path, fa_path],
            output_files_or_dirs=[o.path for o in self.output()]
        )


class PrepareEvaluationIntervals(luigi.WrapperTask):
    evaluation_interval_path = luigi.Parameter()
    cf = luigi.DictParameter()
    priority = 40

    def requires(self):
        return (
            SplitEvaluationIntervals(
                evaluation_interval_path=self.evaluation_interval_path,
                cf=self.cf
            ) if self.cf['split_intervals'] else [
                FetchEvaluationIntervalList(
                    evaluation_interval_path=self.evaluation_interval_path,
                    cf=self.cf
                )
            ]
        )

    def output(self):
        return self.input()


@requires(PrepareCRAMNormal, FetchReferenceFASTA, FetchDbsnpVCF,
          PrepareEvaluationIntervals)
class CallVariantsWithHaplotypeCaller(ShellTask):
    cf = luigi.DictParameter()
    priority = 20

    def output(self):
        return [
            luigi.LocalTarget(
                str(
                    Path(self.cf['germline_snv_indel_gatk_dir_path']).joinpath(
                        Path(self.input()[0][0].path).stem
                        + f'.haplotypecaller.{s}'
                    )
                )
            )
            for s in ['g.vcf.gz', 'g.vcf.gz.tbi', 'cram', 'cram.crai']
        ]

    def run(self):
        gvcf_path = self.output()[0].path
        run_id = '.'.join(Path(gvcf_path).name.split('.')[:-4])
        self.print_log(
            f'Call germline variants with HaplotypeCaller:\t{run_id}'
        )
        gatk = self.cf['gatk']
        gatk_opts = ' --java-options "{}"'.format(self.cf['gatk_java_options'])
        samtools = self.cf['samtools']
        save_memory = str(self.cf['save_memory']).lower()
        n_cpu = self.cf['n_cpu_per_worker']
        memory_per_thread = self.cf['samtools_memory_per_thread']
        input_cram_path = self.input()[0][0].path
        fa_path = self.input()[1][0].path
        dbsnp_vcf_path = self.input()[2][0].path
        evaluation_interval_paths = [i.path for i in self.input()[3]]
        output_cram_path = self.output()[2].path
        if len(evaluation_interval_paths) == 1:
            tmp_bam_paths = [re.sub(r'(\.cram)$', '.bam', output_cram_path)]
            tmp_gvcf_paths = [gvcf_path]
        else:
            tmp_bam_paths = [
                re.sub(
                    r'(\.cram)$', '.{}.bam'.format(Path(i).stem),
                    output_cram_path
                ) for i in evaluation_interval_paths
            ]
            tmp_gvcf_paths = [
                re.sub(
                    r'\.g\.vcf\.gz$', '.{}.g.vcf.gz'.format(Path(i).stem),
                    gvcf_path
                ) for i in evaluation_interval_paths
            ]
        self.setup_shell(
            run_id=run_id, log_dir_path=self.cf['log_dir_path'],
            commands=gatk, cwd=self.cf['germline_snv_indel_gatk_dir_path'],
            remove_if_failed=self.cf['remove_if_failed'],
            env={'REF_CACHE': '.ref_cache'}
        )
        self.run_shell(
            args=[
                (
                    f'set -e && {gatk}{gatk_opts} HaplotypeCaller'
                    + f' --reference {fa_path}'
                    + f' --input {input_cram_path}'
                    + f' --dbsnp {dbsnp_vcf_path}'
                    + f' --intervals {i}'
                    + f' --output {g}'
                    + f' --bam-output {b}'
                    + ' --pair-hmm-implementation AVX_LOGLESS_CACHING_OMP'
                    + f' --native-pair-hmm-threads {n_cpu}'
                    + f' --disable-bam-index-caching {save_memory}'
                    + ' --emit-ref-confidence GVCF'
                    + ''.join(
                        [
                            f' --annotation-group {g}' for g in [
                                'StandardAnnotation', 'AS_StandardAnnotation',
                                'StandardHCAnnotation'
                            ]
                        ] + [
                            f' --gvcf-gq-bands {i}' for i in range(10, 100, 10)
                        ]
                    )
                    + ' --create-output-bam-index false'
                ) for i, g, b in zip(
                    evaluation_interval_paths, tmp_gvcf_paths, tmp_bam_paths
                )
            ],
            input_files_or_dirs=[
                input_cram_path, fa_path, dbsnp_vcf_path,
                *evaluation_interval_paths
            ],
            output_files_or_dirs=[*tmp_gvcf_paths, *tmp_bam_paths],
            asynchronous=(len(evaluation_interval_paths) > 1)
        )
        yield MergeSAMsIntoSortedSAM(
            input_sam_paths=tmp_bam_paths, output_sam_path=output_cram_path,
            fa_path=fa_path, samtools=samtools, n_cpu=n_cpu,
            memory_per_thread=memory_per_thread,
            log_dir_path=self.cf['log_dir_path'],
            remove_if_failed=self.cf['remove_if_failed']
        )
        if len(tmp_gvcf_paths) > 1:
            self.run_shell(
                args=(
                    f'set -e && {gatk}{gatk_opts} CombineGVCFs'
                    + f' --reference {fa_path}'
                    + ''.join([f' --variant {g}' for g in tmp_gvcf_paths])
                    + f' --output {gvcf_path}'
                ),
                input_files_or_dirs=[*tmp_gvcf_paths, fa_path],
                output_files_or_dirs=[gvcf_path, f'{gvcf_path}.tbi']
            )
            if self.cf['remove_if_failed']:
                self.run_shell(
                    args=(
                        'rm -f'
                        + ''.join([f' {p} {p}.tbi' for p in tmp_gvcf_paths])
                    ),
                    input_files_or_dirs=tmp_gvcf_paths
                )


@requires(CallVariantsWithHaplotypeCaller, FetchReferenceFASTA,
          FetchDbsnpVCF, FetchEvaluationIntervalList)
class GenotypeGVCF(ShellTask):
    cf = luigi.DictParameter()
    priority = 60

    def output(self):
        return [
            luigi.LocalTarget(
                re.sub(r'\.g\.vcf\.gz$', s, self.input()[0][0].path)
            ) for s in ['.vcf.gz', '.vcf.gz.tbi']
        ]

    def run(self):
        vcf_path = self.output()[0].path
        run_id = '.'.join(Path(vcf_path).name.split('.')[:-3])
        self.print_log(f'Genotype a HaplotypeCaller GVCF:\t{run_id}')
        gatk = self.cf['gatk']
        gatk_opts = ' --java-options "{}"'.format(self.cf['gatk_java_options'])
        save_memory = str(self.cf['save_memory']).lower()
        gvcf_path = self.input()[0][0].path
        fa_path = self.input()[1][0].path
        dbsnp_vcf_path = self.input()[2][0].path
        evaluation_interval_path = self.input()[3].path
        self.setup_shell(
            run_id=run_id, log_dir_path=self.cf['log_dir_path'], commands=gatk,
            cwd=self.cf['germline_snv_indel_gatk_dir_path'],
            remove_if_failed=self.cf['remove_if_failed']
        )
        self.run_shell(
            args=(
                f'set -e && {gatk}{gatk_opts} GenotypeGVCFs'
                + f' --reference {fa_path}'
                + f' --variant {gvcf_path}'
                + f' --dbsnp {dbsnp_vcf_path}'
                + f' --intervals {evaluation_interval_path}'
                + f' --output {vcf_path}'
                + f' --disable-bam-index-caching {save_memory}'
            ),
            input_files_or_dirs=[
                gvcf_path, fa_path, dbsnp_vcf_path, evaluation_interval_path
            ],
            output_files_or_dirs=[vcf_path, f'{vcf_path}.tbi']
        )


@requires(GenotypeGVCF, CallVariantsWithHaplotypeCaller, FetchReferenceFASTA,
          FetchEvaluationIntervalList)
class CNNScoreVariants(ShellTask):
    cf = luigi.DictParameter()
    priority = 60

    def output(self):
        return [
            luigi.LocalTarget(
                re.sub(r'\.vcf\.gz$', f'.cnn.{s}', self.input()[0][0].path)
            ) for s in ['vcf.gz', 'vcf.gz.tbi']
        ]

    def run(self):
        cnn_vcf_path = self.output()[0].path
        run_id = '.'.join(Path(cnn_vcf_path).name.split('.')[:-4])
        self.print_log(f'Score variants with CNN:\t{run_id}')
        gatk = self.cf['gatk']
        gatk_opts = ' --java-options "{}"'.format(self.cf['gatk_java_options'])
        python3 = self.cf['python3']
        save_memory = str(self.cf['save_memory']).lower()
        raw_vcf_path = self.input()[0][0].path
        cram_path = self.input()[1][2].path
        fa_path = self.input()[2][0].path
        evaluation_interval_path = self.input()[3].path
        self.setup_shell(
            run_id=run_id, log_dir_path=self.cf['log_dir_path'],
            commands=[gatk, python3],
            cwd=self.cf['germline_snv_indel_gatk_dir_path'],
            remove_if_failed=self.cf['remove_if_failed']
        )
        self.run_shell(
            args=(
                f'set -e && {gatk}{gatk_opts} CNNScoreVariants'
                + f' --reference {fa_path}'
                + f' --input {cram_path}'
                + f' --variant {raw_vcf_path}'
                + f' --intervals {evaluation_interval_path}'
                + f' --output {cnn_vcf_path}'
                + ' --tensor-type read_tensor'
                + f' --disable-bam-index-caching {save_memory}'
            ),
            input_files_or_dirs=[
                raw_vcf_path, fa_path, cram_path, evaluation_interval_path
            ],
            output_files_or_dirs=[cnn_vcf_path, f'{cnn_vcf_path}.tbi']
        )


@requires(CNNScoreVariants, FetchHapmapVCF, FetchMillsIndelVCF,
          FetchEvaluationIntervalList)
class FilterVariantTranches(ShellTask):
    cf = luigi.DictParameter()
    snp_tranche = luigi.ListParameter(default=[99.9, 99.95])
    indel_tranche = luigi.ListParameter(default=[99.0, 99.4])
    priority = 60

    def output(self):
        return [
            luigi.LocalTarget(
                re.sub(
                    r'\.vcf\.gz$', f'.filtered.{s}', self.input()[0][0].path
                )
            ) for s in ['vcf.gz', 'vcf.gz.tbi']
        ]

    def run(self):
        filtered_vcf_path = self.output()[0].path
        run_id = '.'.join(Path(filtered_vcf_path).name.split('.')[:-5])
        self.print_log(f'Apply tranche filtering:\t{run_id}')
        gatk = self.cf['gatk']
        gatk_opts = ' --java-options "{}"'.format(self.cf['gatk_java_options'])
        save_memory = str(self.cf['save_memory']).lower()
        cnn_vcf_path = self.input()[0][0].path
        resource_vcf_paths = [self.input()[1][0].path, self.input()[2][0].path]
        evaluation_interval_path = self.input()[3].path
        self.setup_shell(
            run_id=run_id, log_dir_path=self.cf['log_dir_path'], commands=gatk,
            cwd=self.cf['germline_snv_indel_gatk_dir_path'],
            remove_if_failed=self.cf['remove_if_failed']
        )
        self.run_shell(
            args=(
                f'set -e && {gatk}{gatk_opts} FilterVariantTranches'
                + f' --variant {cnn_vcf_path}'
                + ''.join([f' --resource {p}' for p in resource_vcf_paths])
                + f' --intervals {evaluation_interval_path}'
                + f' --output {filtered_vcf_path}'
                + ' --info-key CNN_2D'
                + ''.join(
                    [f' --snp-tranche {v}' for v in self.snp_tranche]
                    + [f' --indel-tranche {v}' for v in self.indel_tranche]
                )
                + ' --invalidate-previous-filters'
                + f' --disable-bam-index-caching {save_memory}'
            ),
            input_files_or_dirs=[
                cnn_vcf_path, *resource_vcf_paths, evaluation_interval_path
            ],
            output_files_or_dirs=[
                filtered_vcf_path, f'{filtered_vcf_path}.tbi'
            ]
        )


if __name__ == '__main__':
    luigi.run()
