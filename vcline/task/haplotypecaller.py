#!/usr/bin/env python

import re
from itertools import chain
from pathlib import Path

import luigi
from luigi.util import requires

from .core import VclineTask
from .cram import PrepareCramNormal
from .resource import (Fetch1000gSnpsVcf, FetchDbsnpVcf,
                       FetchEvaluationIntervalList, FetchHapmapVcf,
                       FetchMillsIndelVcf, FetchReferenceFasta)


@requires(FetchEvaluationIntervalList, FetchReferenceFasta)
class SplitEvaluationIntervals(VclineTask):
    cf = luigi.DictParameter()
    n_cpu = luigi.IntParameter(default=1)
    memory_mb = luigi.FloatParameter(default=4096)
    sh_config = luigi.DictParameter(default=dict())
    priority = 50

    def output(self):
        if self.cf['n_worker'] > 1:
            run_dir = Path(self.cf['qc_dir_path']).joinpath(
                'intervals/{0}.split_in_{1}'.format(
                    Path(self.input()[0].path).stem, self.cf['n_worker']
                )
            )
            return [
                luigi.LocalTarget(
                    run_dir.joinpath(f'{i:04d}-scattered.interval_list')
                ) for i in range(self.cf['n_worker'])
            ]
        else:
            return [luigi.LocalTarget(self.input()[0].path)]

    def run(self):
        input_interval = Path(self.input()[0].path)
        run_id = input_interval.stem
        output_intervals = [Path(o.path) for o in self.output()]
        scatter_count = len(output_intervals)
        self.print_log(f'Split an interval list in {scatter_count}:\t{run_id}')
        fa = Path(self.input()[1][0].path)
        run_dir = output_intervals[0].parent
        gatk = self.cf['gatk']
        self.setup_shell(
            run_id=run_id, commands=gatk, cwd=run_dir, **self.sh_config,
            env={
                'JAVA_TOOL_OPTIONS': self.generate_gatk_java_options(
                    n_cpu=self.n_cpu, memory_mb=self.memory_mb
                )
            }
        )
        self.run_shell(
            args=(
                f'set -e && {gatk} SplitIntervals'
                + f' --reference {fa}'
                + f' --intervals {input_interval}'
                + f' --scatter-count {scatter_count}'
                + f' --output {run_dir}'
            ),
            input_files_or_dirs=[input_interval, fa],
            output_files_or_dirs=[*output_intervals, run_dir]
        )


@requires(PrepareCramNormal, FetchReferenceFasta, FetchDbsnpVcf,
          SplitEvaluationIntervals)
class CallVariantsWithHaplotypeCaller(VclineTask):
    cf = luigi.DictParameter()
    n_cpu = luigi.IntParameter(default=1)
    memory_mb = luigi.FloatParameter(default=4096)
    sh_config = luigi.DictParameter(default=dict())
    priority = 50

    def output(self):
        run_dir = Path(self.cf['germline_snv_indel_gatk_dir_path']).joinpath(
            Path(self.input()[0][0].path).stem
        )
        return [
            luigi.LocalTarget(
                run_dir.joinpath(f'{run_dir.name}.haplotypecaller.{s}')
            ) for s in ['vcf.gz', 'vcf.gz.tbi', 'cram', 'cram.crai']
        ]

    def run(self):
        output_vcf = Path(self.output()[0].path)
        intervals = [Path(i.path) for i in self.input()[3]]
        skip_interval_split = (len(intervals) == 1)
        fa = Path(self.input()[1][0].path)
        input_cram = Path(self.input()[0][0].path)
        dbsnp_vcf = Path(self.input()[2][0].path)
        output_path_prefix = '.'.join(str(output_vcf).split('.')[:-2])
        if skip_interval_split:
            tmp_prefixes = [output_path_prefix]
        else:
            tmp_prefixes = [
                '{0}.{1}'.format(output_path_prefix, o.stem) for o in intervals
            ]
        input_targets = yield [
            HaplotypeCaller(
                input_cram_path=str(input_cram), fa_path=str(fa),
                dbsnp_vcf_path=str(dbsnp_vcf), evaluation_interval_path=str(o),
                output_path_prefix=s, gatk=self.cf['gatk'],
                save_memory=self.cf['save_memory'], n_cpu=self.n_cpu,
                memory_mb=self.memory_mb, sh_config=self.sh_config
            ) for o, s in zip(intervals, tmp_prefixes)
        ]
        run_id = '.'.join(output_vcf.name.split('.')[:-3])
        self.print_log(
            f'Call germline variants with HaplotypeCaller:\t{run_id}'
        )
        output_cram = Path(self.output()[2].path)
        gatk = self.cf['gatk']
        samtools = self.cf['samtools']
        self.setup_shell(
            run_id=run_id, commands=gatk, cwd=output_vcf.parent,
            **self.sh_config,
            env={
                'JAVA_TOOL_OPTIONS': self.generate_gatk_java_options(
                    n_cpu=self.n_cpu, memory_mb=self.memory_mb
                )
            }
        )
        if skip_interval_split:
            tmp_bam = Path(f'{tmp_prefixes[0]}.bam')
            self.samtools_view(
                input_sam_path=tmp_bam, fa_path=fa,
                output_sam_path=output_cram, samtools=samtools,
                n_cpu=self.n_cpu, index_sam=True, remove_input=True
            )
        else:
            tmp_vcfs = [Path(f'{s}.vcf.gz') for s in tmp_prefixes]
            self.run_shell(
                args=(
                    f'set -e && {gatk} MergeVcfs'
                    + ''.join(f' --INPUT {v}' for v in tmp_vcfs)
                    + f' --REFERENCE_SEQUENCE {fa}'
                    + f' --OUTPUT {output_vcf}'
                ),
                input_files_or_dirs=[*tmp_vcfs, fa],
                output_files_or_dirs=[output_vcf, f'{output_vcf}.tbi']
            )
            self.samtools_merge(
                input_sam_paths=[f'{s}.bam' for s in tmp_prefixes],
                fa_path=fa, output_sam_path=output_cram, samtools=samtools,
                n_cpu=self.n_cpu, memory_mb=self.memory_mb, index_sam=True,
                remove_input=False
            )
            self.remove_files_and_dirs(
                *chain.from_iterable(
                    [o.path for o in t] for t in input_targets
                )
            )


class HaplotypeCaller(VclineTask):
    input_cram_path = luigi.Parameter()
    fa_path = luigi.Parameter()
    dbsnp_vcf_path = luigi.Parameter()
    evaluation_interval_path = luigi.Parameter()
    output_path_prefix = luigi.Parameter()
    gatk = luigi.Parameter(default='gatk')
    save_memory = luigi.BoolParameter(default=False)
    message = luigi.Parameter(default='')
    n_cpu = luigi.IntParameter(default=1)
    memory_mb = luigi.FloatParameter(default=4096)
    sh_config = luigi.DictParameter(default=dict())
    priority = 50

    def output(self):
        return [
            luigi.LocalTarget(f'{self.output_path_prefix}.{s}')
            for s in ['vcf.gz', 'vcf.gz.tbi', 'bam']
        ]

    def run(self):
        if self.message:
            self.print_log(self.message)
        input_cram = Path(self.input_cram_path).resolve()
        fa = Path(self.fa_path).resolve()
        dbsnp_vcf = Path(self.dbsnp_vcf_path).resolve()
        evaluation_interval = Path(self.evaluation_interval_path).resolve()
        output_files = [Path(o.path) for o in self.output()]
        output_vcf = output_files[0]
        run_dir = output_vcf.parent
        self.setup_shell(
            run_id='.'.join(output_vcf.name.split('.')[:-2]),
            commands=self.gatk, cwd=run_dir, **self.sh_config,
            env={
                'JAVA_TOOL_OPTIONS': self.generate_gatk_java_options(
                    n_cpu=self.n_cpu, memory_mb=self.memory_mb
                )
            }
        )
        self.run_shell(
            args=(
                f'set -e && {self.gatk} HaplotypeCaller'
                + f' --input {input_cram}'
                + f' --read-index {input_cram}.crai'
                + f' --reference {fa}'
                + f' --dbsnp {dbsnp_vcf}'
                + f' --intervals {evaluation_interval}'
                + f' --output {output_vcf}'
                + f' --bam-output {output_files[2]}'
                + ' --standard-min-confidence-threshold-for-calling 0'
                + ''.join([
                    f' --annotation {g}' for g in [
                        'Coverage', 'ChromosomeCounts', 'BaseQuality',
                        'FragmentLength', 'MappingQuality', 'ReadPosition'
                    ]
                ])
                + f' --native-pair-hmm-threads {self.n_cpu}'
                + ' --create-output-bam-index false'
                + ' --disable-bam-index-caching '
                + str(self.save_memory).lower()
            ),
            input_files_or_dirs=[
                input_cram, f'{input_cram}.crai', fa, dbsnp_vcf,
                evaluation_interval
            ],
            output_files_or_dirs=[*output_files, run_dir]
        )


@requires(CallVariantsWithHaplotypeCaller, FetchReferenceFasta,
          SplitEvaluationIntervals)
class ScoreVariantsWithCnn(VclineTask):
    cf = luigi.DictParameter()
    n_cpu = luigi.IntParameter(default=1)
    memory_mb = luigi.FloatParameter(default=4096)
    sh_config = luigi.DictParameter(default=dict())
    priority = 50

    def output(self):
        output_path_prefix = re.sub(r'\.vcf\.gz$', '', self.input()[0][0].path)
        return [
            luigi.LocalTarget(f'{output_path_prefix}.cnn.vcf.gz{s}')
            for s in ['', '.tbi']
        ]

    def run(self):
        input_vcf = Path(self.input()[0][0].path)
        input_cram = Path(self.input()[0][2].path)
        fa = Path(self.input()[1][0].path)
        intervals = [Path(i.path) for i in self.input()[2]]
        skip_interval_split = (len(intervals) == 1)
        output_vcf = Path(self.output()[0].path)
        output_path_prefix = '.'.join(str(output_vcf).split('.')[:-2])
        if skip_interval_split:
            tmp_prefixes = [output_path_prefix]
        else:
            tmp_prefixes = [
                '{0}.{1}'.format(output_path_prefix, o.stem) for o in intervals
            ]
        input_targets = yield [
            CNNScoreVariants(
                input_vcf_path=str(input_vcf),
                input_cram_path=str(input_cram), fa_path=str(fa),
                evaluation_interval_path=str(o),
                output_path_prefix=s, gatk=self.cf['gatk'],
                python=self.cf['python'], save_memory=self.cf['save_memory'],
                n_cpu=self.n_cpu, memory_mb=self.memory_mb,
                sh_config=self.sh_config
            ) for o, s in zip(intervals, tmp_prefixes)
        ]
        run_id = '.'.join(output_vcf.name.split('.')[:-2])
        self.print_log(f'Score variants with CNN:\t{run_id}')
        gatk = self.cf['gatk']
        self.setup_shell(
            run_id=run_id, commands=gatk, cwd=output_vcf.parent,
            **self.sh_config,
            env={
                'JAVA_TOOL_OPTIONS': self.generate_gatk_java_options(
                    n_cpu=self.n_cpu, memory_mb=self.memory_mb
                )
            }
        )
        if not skip_interval_split:
            tmp_vcfs = [Path(f'{s}.vcf.gz') for s in tmp_prefixes]
            self.run_shell(
                args=(
                    f'set -e && {gatk} MergeVcfs'
                    + ''.join(f' --INPUT {v}' for v in tmp_vcfs)
                    + f' --OUTPUT {output_vcf}'
                ),
                input_files_or_dirs=tmp_vcfs,
                output_files_or_dirs=[output_vcf, f'{output_vcf}.tbi']
            )
            self.remove_files_and_dirs(
                *chain.from_iterable(
                    [o.path for o in t] for t in input_targets
                )
            )


class CNNScoreVariants(VclineTask):
    input_vcf_path = luigi.Parameter()
    input_cram_path = luigi.Parameter()
    fa_path = luigi.Parameter()
    evaluation_interval_path = luigi.Parameter()
    output_path_prefix = luigi.Parameter()
    gatk = luigi.Parameter(default='gatk')
    python = luigi.Parameter(default='python')
    save_memory = luigi.BoolParameter(default=False)
    message = luigi.Parameter(default='')
    n_cpu = luigi.IntParameter(default=1)
    memory_mb = luigi.FloatParameter(default=4096)
    sh_config = luigi.DictParameter(default=dict())
    priority = 50

    def output(self):
        return [
            luigi.LocalTarget(f'{self.output_path_prefix}.vcf.gz' + s)
            for s in ['', '.tbi']
        ]

    def run(self):
        if self.message:
            self.print_log(self.message)
        input_vcf = Path(self.input_vcf_path).resolve()
        input_cram = Path(self.input_cram_path).resolve()
        fa = Path(self.fa_path).resolve()
        evaluation_interval = Path(self.evaluation_interval_path).resolve()
        output_files = [Path(o.path) for o in self.output()]
        output_vcf = output_files[0]
        self.setup_shell(
            run_id='.'.join(output_vcf.name.split('.')[:-2]),
            commands=[self.gatk, self.python], cwd=output_vcf.parent,
            **self.sh_config,
            env={
                'JAVA_TOOL_OPTIONS': self.generate_gatk_java_options(
                    n_cpu=self.n_cpu, memory_mb=self.memory_mb
                )
            }
        )
        self.run_shell(
            args=(
                f'set -e && {self.gatk} CNNScoreVariants'
                + f' --input {input_cram}'
                + f' --variant {input_vcf}'
                + f' --reference {fa}'
                + f' --intervals {evaluation_interval}'
                + f' --output {output_vcf}'
                + ' --tensor-type read_tensor'
                + ' --disable-bam-index-caching '
                + str(self.save_memory).lower()
            ),
            input_files_or_dirs=[
                input_vcf, fa, input_cram, evaluation_interval
            ],
            output_files_or_dirs=output_files
        )


@requires(ScoreVariantsWithCnn, FetchReferenceFasta, FetchHapmapVcf,
          FetchMillsIndelVcf, Fetch1000gSnpsVcf)
class FilterVariantTranches(VclineTask):
    cf = luigi.DictParameter()
    snp_tranches = luigi.ListParameter(default=[99.9, 99.95])
    indel_tranches = luigi.ListParameter(default=[99.0, 99.4])
    n_cpu = luigi.IntParameter(default=1)
    memory_mb = luigi.FloatParameter(default=4096)
    sh_config = luigi.DictParameter(default=dict())
    priority = 50

    def output(self):
        output_path_prefix = re.sub(r'\.vcf\.gz$', '', self.input()[0][0].path)
        return [
            luigi.LocalTarget(f'{output_path_prefix}.filtered.vcf.gz{s}')
            for s in ['', '.tbi']
        ]

    def run(self):
        input_vcf = Path(self.input()[0][0].path)
        run_id = '.'.join(input_vcf.name.split('.')[:-3])
        self.print_log(f'Apply tranche filtering:\t{run_id}')
        resource_vcfs = [Path(i[0].path) for i in self.input()[2:5]]
        output_files = [Path(o.path) for o in self.output()]
        output_vcf = output_files[0]
        gatk = self.cf['gatk']
        self.setup_shell(
            run_id=run_id, commands=gatk, cwd=output_vcf.parent,
            **self.sh_config,
            env={
                'JAVA_TOOL_OPTIONS': self.generate_gatk_java_options(
                    n_cpu=self.n_cpu, memory_mb=self.memory_mb
                )
            }
        )
        self.run_shell(
            args=(
                f'set -e && {gatk} FilterVariantTranches'
                + f' --variant {input_vcf}'
                + ''.join(f' --resource {p}' for p in resource_vcfs)
                + f' --output {output_vcf}'
                + ' --info-key CNN_2D'
                + ''.join(
                    [f' --snp-tranche {v}' for v in self.snp_tranches]
                    + [f' --indel-tranche {v}' for v in self.indel_tranches]
                )
                + ' --invalidate-previous-filters'
                + ' --disable-bam-index-caching '
                + str(self.cf['save_memory']).lower()
            ),
            input_files_or_dirs=[input_vcf, *resource_vcfs],
            output_files_or_dirs=output_files
        )


if __name__ == '__main__':
    luigi.run()
