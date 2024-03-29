#!/usr/bin/env python

import re
import sys
from pathlib import Path

import luigi
from ftarc.task.downloader import DownloadAndProcessResourceFiles

from .callcopyratiosegments import PreprocessIntervals
from .core import VclineTask
from .delly import CreateExclusionIntervalListBed
from .msisensor import ScanMicrosatellites, UncompressEvaluationIntervalListBed
from .resource import CreateCnvBlackListBed, CreateGnomadBiallelicSnpVcf


class DownloadGnomadVcfsAndExtractAf(VclineTask):
    dest_dir_path = luigi.Parameter(default='.')
    use_gnomad_exome = luigi.BoolParameter(default=False)
    cloud_storage = luigi.Parameter(default='amazon')
    wget = luigi.Parameter(default='wget')
    bgzip = luigi.Parameter(default='bgzip')
    tabix = luigi.Parameter(default='tabix')
    picard = luigi.Parameter(default='picard')
    n_cpu = luigi.IntParameter(default=1)
    memory_mb = luigi.FloatParameter(default=4096)
    sh_config = luigi.DictParameter(default=dict())
    priority = 10

    def output(self):
        output_vcf = Path(self.dest_dir_path).resolve().joinpath(
            'gnomad.exomes.r2.1.1.sites.liftover_grch38.af-only.vcf.gz'
            if self.use_gnomad_exome else
            'gnomad.genomes.v3.1.sites.af-only.vcf.gz'
        )
        return [luigi.LocalTarget(f'{output_vcf}{s}') for s in ['', '.tbi']]

    def run(self):
        output_vcf = Path(self.output()[0].path)
        run_id = Path(Path(output_vcf.stem).stem).stem
        self.print_log(f'Download and process a gnomAD VCF file:\t{run_id}')
        dest_dir = output_vcf.parent
        url_root = {
            'google': 'storage.googleapis.com/gcp-public-data--gnomad',
            'amazon': 'gnomad-public-us-east-1.s3.amazonaws.com',
            'microsoft': 'azureopendatastorage.blob.core.windows.net/gnomad'
        }[self.cloud_storage.lower()]
        if self.use_gnomad_exome:
            urls = [
                f'https://{url_root}/release/2.1.1/liftover_grch38/vcf/exomes/'
                + 'gnomad.exomes.r2.1.1.sites.liftover_grch38.vcf.bgz'
            ]
        else:
            urls = [
                (
                    f'https://{url_root}/release/3.1/vcf/genomes/'
                    + f'gnomad.genomes.v3.1.sites.chr{i}.vcf.bgz'
                ) for i in [*range(1, 23), 'X', 'Y']
            ]
        vcf_dict = {
            u: dest_dir.joinpath(Path(Path(u).stem).stem + '.af-only.vcf.gz')
            for u in urls
        }
        pyscript = Path(__file__).resolve().parent.parent.joinpath(
            'script/extract_af_only_vcf.py'
        )
        self.setup_shell(
            run_id=run_id,
            commands=[self.wget, self.bgzip, sys.executable, self.picard],
            cwd=dest_dir, **self.sh_config,
            env={
                'JAVA_TOOL_OPTIONS': self.generate_gatk_java_options(
                    n_cpu=self.n_cpu, memory_mb=self.memory_mb
                )
            }
        )
        for u, v in vcf_dict.items():
            self.run_shell(
                args=(
                    f'set -e && {self.wget} -qSL {u} -O -'
                    + f' | {self.bgzip} -@ {self.n_cpu} -dc'
                    + f' | {sys.executable} {pyscript} -'
                    + f' | {self.bgzip} -@ {self.n_cpu} -c > {v}'
                ),
                output_files_or_dirs=v
            )
        if output_vcf.is_file():
            self.tabix_tbi(tsv_path=output_vcf, tabix=self.tabix, preset='vcf')
        else:
            self.picard_mergevcfs(
                input_vcf_paths=vcf_dict.values(), output_vcf_path=output_vcf,
                picard=self.picard, remove_input=True
            )


class WritePassingAfOnlyVcf(VclineTask):
    src_path = luigi.Parameter(default='')
    src_url = luigi.Parameter(default='')
    dest_dir_path = luigi.Parameter(default='.')
    wget = luigi.Parameter(default='wget')
    bgzip = luigi.Parameter(default='bgzip')
    n_cpu = luigi.IntParameter(default=1)
    sh_config = luigi.DictParameter(default=dict())
    priority = 10

    def output(self):
        return luigi.LocalTarget(
            Path(self.dest_dir_path).resolve().joinpath(
                Path(Path(self.src_path or self.src_url).stem).stem
                + '.af-only.vcf.gz'
            )
        )

    def run(self):
        assert bool(self.src_path or self.src_url)
        output_vcf = Path(self.output().path)
        run_id = Path(Path(output_vcf.stem).stem).stem
        message = (
            'Write a passing AF-only VCF' if self.src_path
            else 'Download a VCF file and extract passing AF-only records'
        )
        self.print_log(f'{message}:\t{run_id}')
        dest_dir = output_vcf.parent
        pyscript = Path(__file__).resolve().parent.parent.joinpath(
            'script/extract_af_only_vcf.py'
        )
        self.setup_shell(
            run_id=run_id,
            commands=[
                *(list() if self.src_path else [self.wget]), self.bgzip,
                sys.executable
            ],
            cwd=dest_dir, **self.sh_config
        )
        if self.src_path:
            src_vcf = Path(self.src_path).resolve()
        else:
            src_vcf = dest_dir.joinpath(Path(self.src_url).name)
            self.run_shell(
                args=f'set -e && {self.wget} -qSL {self.src_url} -O {src_vcf}',
                output_files_or_dirs=src_vcf
            )
        self.run_shell(
            args=(
                f'set -e && {self.bgzip}'
                + f' -@ {self.n_cpu} -dc {src_vcf}'
                + f' | {sys.executable} {pyscript} -'
                + f' | {self.bgzip} -@ {self.n_cpu} -c > {output_vcf}'
            ),
            input_files_or_dirs=src_vcf, output_files_or_dirs=output_vcf
        )


class CreateWgsIntervalList(VclineTask):
    fa_path = luigi.Parameter()
    dest_dir_path = luigi.Parameter(default='.')
    gatk = luigi.Parameter(default='gatk')
    memory_mb = luigi.FloatParameter(default=4096)
    sh_config = luigi.DictParameter(default=dict())
    priority = 10

    def output(self):
        return luigi.LocalTarget(
            Path(self.dest_dir_path).resolve().joinpath(
                Path(self.fa_path).stem + '.wgs.interval_list'
            )
        )

    def run(self):
        fa = Path(self.fa_path).resolve()
        run_id = fa.stem
        self.print_log(f'Create a WGS interval list:\t{run_id}')
        output_interval = Path(self.output().path)
        dest_dir = output_interval.parent
        raw_interval = dest_dir.joinpath(f'{fa.stem}.raw.interval_list')
        self.setup_shell(
            run_id=run_id, commands=self.gatk, cwd=dest_dir, **self.sh_config,
            env={'JAVA_TOOL_OPTIONS': '-Xmx{}m'.format(int(self.memory_mb))}
        )
        self.run_shell(
            args=(
                f'set -e && {self.gatk} ScatterIntervalsByNs'
                + f' --REFERENCE {fa}'
                + ' --OUTPUT_TYPE ACGT'
                + f' --OUTPUT {raw_interval}'
            ),
            input_files_or_dirs=fa, output_files_or_dirs=raw_interval
        )
        self.run_shell(
            args=(
                'set -e && grep'
                + ' -e \'^@\' -e \'^chr[0-9XYM]\\+\\s\''
                + f' {raw_interval} > {output_interval}'
            ),
            input_files_or_dirs=raw_interval,
            output_files_or_dirs=output_interval
        )
        self.remove_files_and_dirs(raw_interval)


class PreprocessResources(luigi.Task):
    src_url_dict = luigi.DictParameter()
    dest_dir_path = luigi.Parameter(default='.')
    use_gnomad_exome = luigi.BoolParameter(default=False)
    use_bwa_mem2 = luigi.BoolParameter(default=False)
    wget = luigi.Parameter(default='wget')
    bgzip = luigi.Parameter(default='bgzip')
    pbzip2 = luigi.Parameter(default='pbzip2')
    pigz = luigi.Parameter(default='pigz')
    bwa = luigi.Parameter(default='bwa')
    samtools = luigi.Parameter(default='samtools')
    tabix = luigi.Parameter(default='tabix')
    gatk = luigi.Parameter(default='gatk')
    bedtools = luigi.Parameter(default='bedtools')
    msisensor = luigi.Parameter(default='msisensor')
    n_cpu = luigi.IntParameter(default=1)
    memory_mb = luigi.FloatParameter(default=4096)
    sh_config = luigi.DictParameter(default=dict())
    priority = 10

    def requires(self):
        return [
            DownloadAndProcessResourceFiles(
                src_urls=list(self.src_url_dict.values()),
                dest_dir_path=self.dest_dir_path, wget=self.wget,
                bgzip=self.bgzip, pbzip2=self.pbzip2, pigz=self.pigz,
                bwa=self.bwa, samtools=self.samtools, tabix=self.tabix,
                gatk=self.gatk, n_cpu=self.n_cpu, memory_mb=self.memory_mb,
                use_bwa_mem2=self.use_bwa_mem2, sh_config=self.sh_config
            ),
            DownloadGnomadVcfsAndExtractAf(
                dest_dir_path=self.dest_dir_path,
                use_gnomad_exome=self.use_gnomad_exome, wget=self.wget,
                bgzip=self.bgzip, picard=self.gatk, tabix=self.tabix,
                n_cpu=self.n_cpu, memory_mb=self.memory_mb,
                sh_config=self.sh_config
            )
        ]

    def output(self):
        path_dict = self._fetch_input_path_dict()
        fa = Path(path_dict['ref_fa'])
        interval = fa.parent.joinpath(f'{fa.stem}.wgs.interval_list')
        gnomad_vcf = Path(path_dict['gnomad_vcf'])
        cnv_blacklist = Path(path_dict['cnv_blacklist'])
        return [
            *self.input(),
            *[
                luigi.LocalTarget(
                    interval.parent.joinpath(interval.stem + s)
                ) for s in [
                    '.bed', '.bed.gz', '.bed.gz.tbi', '.exclusion.bed.gz',
                    '.exclusion.bed.gz.tbi', '.preprocessed.wes.interval_list',
                    '.preprocessed.wgs.interval_list'
                ]
            ],
            *[
                luigi.LocalTarget(
                    gnomad_vcf.parent.joinpath(
                        Path(gnomad_vcf.stem).stem + f'.biallelic_snp{s}'
                    )
                ) for s in ['.vcf.gz', '.vcf.gz.tbi', '.interval_list']
            ],
            *[
                luigi.LocalTarget(
                    cnv_blacklist.parent.joinpath(cnv_blacklist.stem + s)
                ) for s in ['.bed.gz', '.bed.gz.tbi']
            ],
            luigi.LocalTarget(
                fa.parent.joinpath(fa.stem + '.microsatellites.tsv')
            )
        ]

    def run(self):
        path_dict = self._fetch_input_path_dict()
        evaluation_interval_target = yield CreateWgsIntervalList(
            fa_path=path_dict['ref_fa'], dest_dir_path=self.dest_dir_path,
            gatk=self.gatk, memory_mb=self.memory_mb, sh_config=self.sh_config
        )
        evaluation_interval_path = evaluation_interval_target.path
        cf = {
            'pigz': self.pigz, 'pbzip2': self.pbzip2, 'bgzip': self.bgzip,
            'bwa': self.bwa, 'samtools': self.samtools, 'tabix': self.tabix,
            'gatk': self.gatk, 'bedtools': self.bedtools,
            'msisensor': self.msisensor, 'use_bwa_mem2': self.use_bwa_mem2
        }
        yield [
            CreateExclusionIntervalListBed(
                evaluation_interval_path=evaluation_interval_path,
                ref_fa_path=path_dict['ref_fa'], cf=cf, n_cpu=self.n_cpu,
                sh_config=self.sh_config
            ),
            CreateGnomadBiallelicSnpVcf(
                gnomad_vcf_path=path_dict['gnomad_vcf'],
                ref_fa_path=path_dict['ref_fa'],
                evaluation_interval_path=evaluation_interval_path,
                cf=cf, n_cpu=self.n_cpu, memory_mb=self.memory_mb,
                sh_config=self.sh_config
            ),
            CreateCnvBlackListBed(
                cnv_blacklist_path=path_dict['cnv_blacklist'], cf=cf,
                n_cpu=self.n_cpu, sh_config=self.sh_config
            ),
            *[
                PreprocessIntervals(
                    ref_fa_path=path_dict['ref_fa'],
                    evaluation_interval_path=evaluation_interval_path,
                    cnv_blacklist_path=path_dict['cnv_blacklist'],
                    cf={'exome': bool(i), **cf}, n_cpu=self.n_cpu,
                    memory_mb=self.memory_mb, sh_config=self.sh_config
                ) for i in range(2)
            ],
            ScanMicrosatellites(
                ref_fa_path=path_dict['ref_fa'], cf=cf,
                sh_config=self.sh_config
            ),
            UncompressEvaluationIntervalListBed(
                evaluation_interval_path=evaluation_interval_path, cf=cf,
                n_cpu=self.n_cpu, sh_config=self.sh_config
            )
        ]

    def _fetch_input_path_dict(self):
        dest_dir = Path(self.dest_dir_path).resolve()
        return {
            **{
                k: re.sub(
                    r'\.(gz|bz2)$', '',
                    str(dest_dir.joinpath(Path(self.src_url_dict[k]).name))
                ) for k in ['ref_fa', 'evaluation_interval', 'cnv_blacklist']
            },
            'gnomad_vcf': self.input()[1][0].path
        }


if __name__ == '__main__':
    luigi.run()
