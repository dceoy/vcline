#!/usr/bin/env python

from pathlib import Path

import luigi
from luigi.util import requires

from ..cli.util import print_log
from .base import ShellTask
from .ref import CreateBWAIndexes, CreateFASTAIndex, FetchGenomeFASTA
from .trim import TrimAdapters, parse_fq_id


@requires(FetchGenomeFASTA, CreateBWAIndexes, CreateFASTAIndex, TrimAdapters)
class AlignReads(ShellTask):
    p = luigi.DictParameter()
    priority = 10

    def output(self):
        return [
            luigi.LocalTarget(
                str(
                    Path(self.p['align_dir_path']).joinpath(
                        '{0}.trimmed.{1}.cram{2}'.format(
                            parse_fq_id(fq_path=self.input()[3][0].path),
                            Path(Path(self.input()[0].path).name).stem, s
                        )
                    )
                )
            ) for s in ['', '.crai']
        ]

    def run(self):
        cram_path = self.output()[0].path
        run_id = Path(Path(cram_path).name).stem
        print_log(f'Align reads:\t{run_id}')
        bwa = self.p['bwa']
        samtools = self.p['samtools']
        n_cpu = self.p['n_cpu_per_worker']
        r = '\'@RG\\tID:None\\tSM:None\\tPL:ILLUMINA\\tLB:None\''
        fa_path = self.input()[0].path
        index_paths = [o.path for o in [*self.input()[1], self.input()[2]]]
        fq_paths = [i.path for i in self.input()[3]]
        self.bash_c(
            args=[
                f'{bwa} 2>&1 | grep -e "Version:"',
                f'{samtools} 2>&1 | grep -e "Version:"',
                (
                    'set -eo pipefail && '
                    + f'{bwa} mem -t {n_cpu} -R {r} {fa_path} '
                    + ' '.join(fq_paths)
                    + f' | {samtools} view -@ {n_cpu} -T {fa_path} -CS - -o -'
                    + f' | {samtools} sort -@ {n_cpu} -T {cram_path}.tmp'
                    + f' -o {cram_path} -'
                ),
                f'set -e && {samtools} index -@ {n_cpu} {cram_path}'
            ],
            input_files=[fa_path, *index_paths, *fq_paths],
            output_files=[o.path for o in self.output()],
            cwd=self.p['align_dir_path'], run_id=run_id,
            log_dir_path=self.p['log_dir_path']
        )


@requires(FetchGenomeFASTA, CreateFASTAIndex, AlignReads)
class MarkDuplicates(ShellTask):
    p = luigi.DictParameter()
    priority = 10

    def output(self):
        return [
            luigi.LocalTarget(
                str(Path(self.input()[0].path).stem) + f'.markdup.cram{s}'
            ) for s in ['', '.crai']
        ]

    def run(self):
        input_cram_path = self.input()[0].path
        run_id = Path(Path(input_cram_path).name).stem
        print_log(f'Mark duplicates:\t{run_id}')
        gatk = self.p['gatk']
        samtools = self.p['samtools']
        n_cpu = self.p['n_cpu_per_worker']
        fa_path = self.input()[0].path
        fai_path = self.input()[1].path
        output_cram_path = self.output()[0].path
        prefix = '{}.markdup'.format(Path(input_cram_path).stem)
        self.bash_c(
            args=[
                (
                    'set -eo pipefail && '
                    + f'{gatk} MarkDuplicates'
                    + f' INPUT={input_cram_path}'
                    + f' OUTPUT=/dev/stdout'
                    + f' METRICS_FILE={prefix}.markdup_metrics.txt'
                    + ' VALIDATION_STRINGENCY=SILENT'
                    + ' OPTICAL_DUPLICATE_PIXEL_DISTANCE=2500'
                    + ' ASSUME_SORTED=true'
                    + ' CREATE_MD5_FILE=true'
                    + f' | {samtools} sort -@ {n_cpu} -T {prefix}.tmp -'
                    + f' | {gatk} SetNmMdAndUqTags'
                    + ' INPUT=/dev/stdin'
                    + ' OUTPUT=/dev/stdout'
                    + ' REFERENCE_SEQUENCE={fa_path}'
                    + f' | {samtools} view -@ {n_cpu} -T {fa_path} -CS -'
                    + f' -o {output_cram_path}'
                ),
                f'set -e && {samtools} index -@ {n_cpu} {output_cram_path}'
            ],
            input_files=[input_cram_path, fa_path, fai_path],
            output_files=[o.path for o in self.output()],
            cwd=self.p['align_dir_path'],
            run_id=run_id, log_dir_path=self.p['log_dir_path']
        )


if __name__ == '__main__':
    luigi.run()