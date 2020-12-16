#!/usr/bin/env python
"""
Variant Calling Pipeline for Clinical Sequencing

Usage:
    vcline init [--debug|--info] [--yml=<path>]
    vcline run [--debug|--info] [--yml=<path>] [--cpus=<int>]
        [--workers=<int>] [--skip-cleaning] [--print-subprocesses]
        [--use-bwa-mem2] [--ref-dir=<path>] [--dest-dir=<path>]
    vcline preprocess [--debug|--info] [--yml=<path>] [--cpus=<int>]
        [--workers=<int>] [--skip-cleaning] [--print-subprocesses]
        [--use-bwa-mem2] [--ref-dir=<path>] [--dest-dir=<path>]
    vcline download-resources [--debug|--info] [--cpus=<int>]
        [--without-gnomad] [--dest-dir=<path>]
    vcline download-and-preprocess [--debug|--info] [--cpus=<int>]
        [--workers=<int>] [--skip-cleaning] [--print-subprocesses]
        [--use-bwa-mem2] [--dest-dir=<path>]
    vcline download-funcotator-data [--debug|--info] [--cpus=<int>]
        [--dest-dir=<path>]
    vcline download-snpeff-data [--debug|--info] [--dest-dir=<path>]
    vcline download-gnomad-af-only-vcf [--debug|--info] [--cpus=<int>]
        [--dest-dir=<path>]
    vcline write-af-only-vcf [--debug|--info] [--cpus=<int>]
        [--src-path=<path>|--src-url=<url>] [--dest-dir=<path>]
    vcline create-interval-list [--debug|--info] [--cpus=<int>]
        [--dest-dir=<path>] <fa_path> <bed_path>
    vcline funcotator [--debug|--info] [--cpus=<int>] [--ref-ver=<str>]
        [--normalize-vcf] [--output-format=<str>] [--dest-dir=<path>]
        <data_dir_path> <fa_path> <vcf_path>...
    vcline snpeff [--debug|--info] [--cpus=<int>] [--ref-ver=<str>]
        [--snpeff-genome=<ver>] [--normalize-vcf] [--dest-dir=<path>]
        <snpeff_config_path> <fa_path> <vcf_path>...
    vcline -h|--help
    vcline --version

Commands:
    init                    Create a config YAML template
    run                     Run the analytical pipeline
    preprocess              Run the resource preprocessing
    download-resources      Download and process resource data
    download-and-preprocess
                            Download and preprocess resources
    download-funcotator-data
                            Download Funcotator data sources
    download-snpeff-data    Download snpEff data sources
    download-gnomad-af-only-vcf
                            Download gnomAD VCF and process it into AF-only VCF
    write-af-only-vcf       Extract and write only AF from VCF INFO
    create-interval-list    Create an interval_list from BED
    funcotator              Create annotated VCFs using Funcotator
    snpeff                  Create annotated VCFs using SnpEff

Options:
    -h, --help              Print help and exit
    --version               Print version and exit
    --debug, --info         Execute a command with debug|info messages
    --yml=<path>            Specify a config YAML path [default: vcline.yml]
    --cpus=<int>            Limit CPU cores used
    --workers=<int>         Specify the maximum number of workers [default: 1]
    --skip-cleaning         Skip incomlete file removal when a task fails
    --print-subprocesses    Print STDOUT/STDERR outputs from subprocesses
    --use-bwa-mem2          Use BWA-MEM2 for read alignment
    --ref-dir=<path>        Specify a reference directory path
    --dest-dir=<path>       Specify a destination directory path [default: .]
    --without-gnomad        Skip downloading gnomAD VCF (>200GB)
    --src-url=<url>         Specify a source URL
    --src-path=<path>       Specify a source path
    --ref-ver=<str>         Specify a reference version [default: hg38]
    --snpeff-genome=<ver>   Specify a SnpEff genome version
                            (overriding --ref-ver)
    --normalize-vcf         Normalize VCF files
    --output-format=<str>   Specify output file format [default: VCF]

Args:
    <fa_path>               Path to an reference FASTA file
                            (The index and sequence dictionary are required.)
    <bed_path>              Path to a BED file
    <data_dir_path>         Path to a Funcotator data source directory
    <vcf_path>              Path to a VCF file
    <snpeff_config_path>    Path to a SnpEff config file
"""

import logging
import os
from math import floor
from pathlib import Path

from docopt import docopt
from ftarc.task.downloader import DownloadResourceFiles
from psutil import cpu_count, virtual_memory

from .. import __version__
from ..task.download import (DownloadAndConvertVCFsIntoPassingAfOnlyVCF,
                             DownloadFuncotatorDataSources,
                             DownloadSnpEffDataSource, WritePassingAfOnlyVCF)
from ..task.funcotator import Funcotator
from ..task.ref import CreateIntervalListWithBED
from ..task.snpeff import SnpEff
from .builder import build_luigi_tasks, run_analytical_pipeline
from .util import (fetch_executable, load_default_dict, render_template,
                   write_config_yml)


def main():
    args = docopt(__doc__, version=__version__)
    if args['--debug']:
        log_level = 'DEBUG'
    elif args['--info']:
        log_level = 'INFO'
    else:
        log_level = 'WARNING'
    logging.basicConfig(
        format='%(asctime)s %(levelname)-8s %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S', level=log_level
    )
    logger = logging.getLogger(__name__)
    logger.debug(f'args:{os.linesep}{args}')
    if args['init']:
        write_config_yml(path=args['--yml'])
    elif args['run'] or args['preprocess']:
        run_analytical_pipeline(
            config_yml_path=args['--yml'], ref_dir_path=args['--ref-dir'],
            dest_dir_path=args['--dest-dir'], max_n_cpu=args['--cpus'],
            max_n_worker=args['--workers'],
            skip_cleaning=args['--skip-cleaning'],
            print_subprocesses=args['--print-subprocesses'],
            console_log_level=log_level, only_preprocessing=args['preprocess'],
            use_bwa_mem2=args['--use-bwa-mem2']
        )
    else:
        dest_dir_path = str(Path(args['--dest-dir']).resolve())
        n_cpu = int(args['--cpus'] or cpu_count())
        if args['download-resources'] or args['download-and-preprocess']:
            url_dict = load_default_dict(stem='urls')
            cmds = {
                c: fetch_executable(c)
                for c in ['wget', 'pbzip2', 'bgzip', 'gatk', 'snpEff']
            }
            build_luigi_tasks(
                tasks=[
                    DownloadResourceFiles(
                        src_url=[
                            v for k, v in url_dict.items() if k != 'gnomad_vcf'
                        ],
                        dest_dir_path=dest_dir_path,
                        n_cpu=n_cpu, wget=cmds['wget'],
                        pbzip2=cmds['pbzip2'], bgzip=cmds['bgzip']
                    ),
                    DownloadFuncotatorDataSources(
                        dest_dir_path=dest_dir_path, n_cpu=n_cpu,
                        gatk=cmds['gatk']
                    ),
                    DownloadSnpEffDataSource(
                        dest_dir_path=dest_dir_path, snpeff=cmds['snpEff']
                    ),
                    *(
                        list() if args['--without-gnomad'] else [
                            DownloadAndConvertVCFsIntoPassingAfOnlyVCF(
                                src_url=url_dict['gnomad_vcf'],
                                dest_dir_path=dest_dir_path, n_cpu=n_cpu,
                                wget=cmds['wget'], bgzip=cmds['bgzip']
                            )
                        ]
                    )
                ],
                log_level=log_level
            )
            if args['download-and-preprocess']:
                ref_dir = Path(args['--dest-dir']).resolve()
                ref_dir_path = str(ref_dir)
                vcline_yml_path = str(ref_dir.joinpath('resource_vcline.yml'))
                render_template(
                    template='resource_vcline.yml.j2',
                    data={'resource_dir_path': ref_dir_path},
                    output_path=vcline_yml_path
                )
                run_analytical_pipeline(
                    config_yml_path=vcline_yml_path, ref_dir_path=ref_dir_path,
                    dest_dir_path=ref_dir_path, max_n_cpu=args['--cpus'],
                    max_n_worker=args['--workers'],
                    skip_cleaning=args['--skip-cleaning'],
                    print_subprocesses=args['--print-subprocesses'],
                    console_log_level=log_level, only_preprocessing=True,
                    use_bwa_mem2=args['--use-bwa-mem2']
                )
        elif args['download-funcotator-data']:
            build_luigi_tasks(
                tasks=[
                    DownloadFuncotatorDataSources(
                        dest_dir_path=dest_dir_path, n_cpu=n_cpu,
                        gatk=fetch_executable('gatk')
                    )
                ],
                log_level=log_level
            )
        elif args['download-snpeff-data']:
            build_luigi_tasks(
                tasks=[
                    DownloadSnpEffDataSource(
                        dest_dir_path=dest_dir_path,
                        snpeff=fetch_executable('snpEff')
                    )
                ],
                log_level=log_level
            )
        elif args['download-gnomad-af-only-vcf']:
            build_luigi_tasks(
                tasks=[
                    DownloadAndConvertVCFsIntoPassingAfOnlyVCF(
                        src_url=load_default_dict(stem='urls')['gnomad_vcf'],
                        dest_dir_path=dest_dir_path, n_cpu=n_cpu,
                        **{c: fetch_executable(c) for c in ['wget', 'bgzip']}
                    )
                ],
                log_level=log_level
            )
        elif args['write-af-only-vcf']:
            build_luigi_tasks(
                tasks=[
                    WritePassingAfOnlyVCF(
                        src_path=(
                            str(Path(args['--src-path']).resolve())
                            if args['--src-path'] else ''
                        ),
                        dest_dir_path=dest_dir_path, n_cpu=n_cpu,
                        **(
                            {'src_url': args['--src-url']}
                            if args['--src-url'] else dict()
                        ),
                        **{c: fetch_executable(c) for c in ['curl', 'bgzip']}
                    )
                ],
                log_level=log_level
            )
        elif args['create-interval-list']:
            build_luigi_tasks(
                tasks=[
                    CreateIntervalListWithBED(
                        fa_path=str(Path(args['<fa_path>']).resolve()),
                        bed_path=str(Path(args['<bed_path>']).resolve()),
                        dest_dir_path=dest_dir_path, n_cpu=n_cpu,
                        gatk=fetch_executable('gatk')
                    )
                ],
                log_level=log_level
            )
        elif args['funcotator'] or args['snpeff']:
            n_vcf = len(args['<vcf_path>'])
            n_worker = min(n_vcf, n_cpu)
            common_kwargs = {
                'fa_path': str(Path(args['<fa_path>']).resolve()),
                'ref_version': args['--ref-ver'],
                'dest_dir_path': dest_dir_path,
                'normalize_vcf': args['--normalize-vcf'],
                'n_cpu': (floor(n_cpu / n_vcf) if n_cpu > n_vcf else 1),
                'memory_mb':
                int(virtual_memory().total / 1024 / 1024 / n_worker)
            }
            if args['funcotator']:
                kwargs = {
                    'data_src_dir_path':
                    str(Path(args['<data_dir_path>']).resolve()),
                    'output_file_format': args['--output-format'],
                    **{c: fetch_executable(c) for c in ['gatk', 'bcftools']},
                    **common_kwargs
                }
                build_luigi_tasks(
                    tasks=[
                        Funcotator(
                            input_vcf_path=str(Path(p).resolve()), **kwargs
                        ) for p in args['<vcf_path>']
                    ],
                    workers=n_worker, log_level=log_level
                )
            else:
                kwargs = {
                    'snpeff_config_path':
                    str(Path(args['<snpeff_config_path>']).resolve()),
                    'snpeff_genome_version': args['--snpeff-genome'],
                    **{
                        c.lower(): fetch_executable(c)
                        for c in ['snpEff', 'bcftools', 'bgzip', 'tabix']
                    },
                    **common_kwargs
                }
                build_luigi_tasks(
                    tasks=[
                        SnpEff(input_vcf_path=str(Path(p).resolve()), **kwargs)
                        for p in args['<vcf_path>']
                    ],
                    workers=n_worker, log_level=log_level
                )
