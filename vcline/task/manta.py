#!/usr/bin/env python

from itertools import product
from math import floor
from pathlib import Path

import luigi
from luigi.util import requires

from ..cli.util import create_matched_id
from .align import PrepareCRAMs
from .base import ShellTask
from .ref import (CreateEvaluationIntervalListBED, CreateFASTAIndex,
                  FetchReferenceFASTA)


@requires(PrepareCRAMs, FetchReferenceFASTA, CreateFASTAIndex,
          CreateEvaluationIntervalListBED)
class CallStructualVariantsWithManta(ShellTask):
    cf = luigi.DictParameter()
    priority = 10

    def output(self):
        return [
            luigi.LocalTarget(
                str(
                    Path(self.cf['manta_dir_path']).joinpath(
                        create_matched_id(
                            *[i[0].path for i in self.input()[0]]
                        ) + f'.manta.{v}{s}'
                    )
                )
            ) for v, s in product(
                [
                    'somaticSV.vcf.gz', 'diploidSV.vcf.gz',
                    'candidateSV.vcf.gz', 'candidateSmallIndels.vcf.gz'
                ],
                ['', '.tbi']
            )
        ]

    def run(self):
        run_id = '.'.join(Path(self.output()[0].path).name.split('.')[:-4])
        self.print_log(f'Call somatic SVs with Manta:\t{run_id}')
        config_script = self.cf['configManta.py']
        run_dir_path = str(Path(self.cf['manta_dir_path']).joinpath(run_id))
        run_script = str(Path(run_dir_path).joinpath('runWorkflow.py'))
        python2 = self.cf['python2']
        n_cpu = self.cf['n_cpu_per_worker']
        memory_gb = max(floor(self.cf['memory_mb_per_worker'] / 1024), 1)
        input_cram_paths = [i[0].path for i in self.input()[0]]
        fa_path = self.input()[1].path
        fai_path = self.input()[2].path
        bed_path = self.input()[3][0].path
        pythonpath = Path(config_script).parent.parent.joinpath('lib/python')
        vcf_dir_path = str(Path(run_dir_path).joinpath('results/variants'))
        self.setup_shell(
            run_id=run_id, log_dir_path=self.cf['log_dir_path'],
            commands=[python2, config_script], cwd=self.cf['manta_dir_path'],
            remove_if_failed=self.cf['remove_if_failed']
        )
        self.run_shell(
            args=(
                f'set -e && PYTHONPATH="{pythonpath}" && {config_script}'
                + f' --tumorBam={input_cram_paths[0]}'
                + f' --normalBam={input_cram_paths[1]}'
                + f' --referenceFasta={fa_path}'
                + f' --callRegions={bed_path}'
                + f' --runDir={run_dir_path}'
                + (' --exome' if self.cf['exome'] else '')
            ),
            input_files_or_dirs=[
                *input_cram_paths, fa_path, fai_path, bed_path
            ],
            output_files_or_dirs=[run_script, run_dir_path]
        )
        self.run_shell(
            args=(
                f'set -e && PYTHONPATH="{pythonpath}" && {run_script}'
                + f' --jobs={n_cpu}'
                + f' --memGb={memory_gb}'
                + ' --mode=local'
            ),
            input_files_or_dirs=[
                run_script, *input_cram_paths, fa_path, fai_path, bed_path
            ],
            output_files_or_dirs=run_dir_path
        )
        self.run_shell(
            args=[
                f'ln -s {run_id}/results/variants/{o.name} {run_id}.{o.name}'
                for o in Path(vcf_dir_path).iterdir()
            ],
            input_files_or_dirs=vcf_dir_path,
            output_files_or_dirs=[o.path for o in self.output()]
        )


if __name__ == '__main__':
    luigi.run()
