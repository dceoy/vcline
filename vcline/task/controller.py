#!/usr/bin/env python

import logging
import os
import sys
from itertools import chain
from pathlib import Path
from socket import gethostname

import luigi
from luigi.tools import deps_tree
from vanqc.task.bcftools import NormalizeVcf
from vanqc.task.gatk import (AnnotateSegWithFuncotateSegments,
                             AnnotateVariantsWithFuncotator)
from vanqc.task.snpeff import AnnotateVariantsWithSnpeff
from vanqc.task.vep import AnnotateVariantsWithEnsemblVep

from .callcopyratiosegments import CallCopyRatioSegmentsMatched
from .core import VclineTask
from .delly import CallStructualVariantsWithDelly
from .haplotypecaller import FilterVariantTranches
from .manta import CallStructualVariantsWithManta
from .msisensorpro import ScoreMsiWithMsisensorPro
from .mutect2 import FilterMutectCalls
from .strelka import (CallGermlineVariantsWithStrelka,
                      CallSomaticVariantsWithStrelka)


class PrintEnvVersions(VclineTask):
    command_paths = luigi.ListParameter(default=list())
    run_id = luigi.Parameter(default=gethostname())
    sh_config = luigi.DictParameter(default=dict())
    __is_completed = False

    def complete(self):
        return self.__is_completed

    def run(self):
        python = sys.executable
        self.print_log(f'Print environment versions: {python}')
        version_files = [
            Path('/proc/version'),
            *[
                o for o in Path('/etc').iterdir()
                if o.name.endswith(('-release', '_version'))
            ]
        ]
        self.setup_shell(
            run_id=self.run_id, commands=[python, *self.command_paths],
            **self.sh_config
        )
        self.run_shell(
            args=[
                f'{python} -m pip --version',
                f'{python} -m pip freeze --no-cache-dir'
            ]
        )
        self.run_shell(
            args=[
                'uname -a',
                *[f'cat {o}' for o in version_files if o.is_file()]
            ]
        )
        self.__is_completed = True


class RunVariantCaller(luigi.Task):
    ref_fa_path = luigi.Parameter()
    fq_list = luigi.ListParameter()
    cram_list = luigi.ListParameter()
    read_groups = luigi.ListParameter()
    sample_names = luigi.ListParameter()
    cf = luigi.DictParameter()
    dbsnp_vcf_path = luigi.Parameter(default='')
    mills_indel_vcf_path = luigi.Parameter(default='')
    known_indel_vcf_path = luigi.Parameter(default='')
    hapmap_vcf_path = luigi.Parameter(default='')
    gnomad_vcf_path = luigi.Parameter(default='')
    evaluation_interval_path = luigi.Parameter(default='')
    cnv_blacklist_path = luigi.Parameter(default='')
    funcotator_somatic_data_dir_path = luigi.Parameter(default='')
    funcotator_germline_data_dir_path = luigi.Parameter(default='')
    snpeff_data_dir_path = luigi.Parameter(default='')
    vep_cache_dir_path = luigi.Parameter(default='')
    caller = luigi.Parameter(default='')
    annotators = luigi.ListParameter(default=list())
    normalize_vcf = luigi.BoolParameter(default=True)
    n_cpu = luigi.IntParameter(default=1)
    memory_mb = luigi.FloatParameter(default=4096)
    sh_config = luigi.DictParameter(default=dict())
    priority = luigi.IntParameter(default=1000)

    def requires(self):
        if 'germline_snv_indel.gatk' == self.caller:
            return FilterVariantTranches(
                fq_list=self.fq_list, cram_list=self.cram_list,
                read_groups=self.read_groups, sample_names=self.sample_names,
                ref_fa_path=self.ref_fa_path,
                dbsnp_vcf_path=self.dbsnp_vcf_path,
                mills_indel_vcf_path=self.mills_indel_vcf_path,
                known_indel_vcf_path=self.known_indel_vcf_path,
                hapmap_vcf_path=self.hapmap_vcf_path,
                evaluation_interval_path=self.evaluation_interval_path,
                cf=self.cf, n_cpu=self.n_cpu, memory_mb=self.memory_mb,
                sh_config=self.sh_config
            )
        elif 'somatic_snv_indel.gatk' == self.caller:
            return FilterMutectCalls(
                fq_list=self.fq_list, cram_list=self.cram_list,
                read_groups=self.read_groups, sample_names=self.sample_names,
                ref_fa_path=self.ref_fa_path,
                dbsnp_vcf_path=self.dbsnp_vcf_path,
                mills_indel_vcf_path=self.mills_indel_vcf_path,
                known_indel_vcf_path=self.known_indel_vcf_path,
                gnomad_vcf_path=self.gnomad_vcf_path,
                evaluation_interval_path=self.evaluation_interval_path,
                cf=self.cf, n_cpu=self.n_cpu, memory_mb=self.memory_mb,
                sh_config=self.sh_config
            )
        elif 'somatic_sv.manta' == self.caller:
            return CallStructualVariantsWithManta(
                fq_list=self.fq_list, cram_list=self.cram_list,
                read_groups=self.read_groups, sample_names=self.sample_names,
                ref_fa_path=self.ref_fa_path,
                dbsnp_vcf_path=self.dbsnp_vcf_path,
                mills_indel_vcf_path=self.mills_indel_vcf_path,
                known_indel_vcf_path=self.known_indel_vcf_path,
                evaluation_interval_path=self.evaluation_interval_path,
                cf=self.cf, n_cpu=self.n_cpu, memory_mb=self.memory_mb,
                sh_config=self.sh_config
            )
        elif 'somatic_snv_indel.strelka' == self.caller:
            return CallSomaticVariantsWithStrelka(
                fq_list=self.fq_list, cram_list=self.cram_list,
                read_groups=self.read_groups, sample_names=self.sample_names,
                ref_fa_path=self.ref_fa_path,
                dbsnp_vcf_path=self.dbsnp_vcf_path,
                mills_indel_vcf_path=self.mills_indel_vcf_path,
                known_indel_vcf_path=self.known_indel_vcf_path,
                evaluation_interval_path=self.evaluation_interval_path,
                cf=self.cf, n_cpu=self.n_cpu, memory_mb=self.memory_mb,
                sh_config=self.sh_config
            )
        elif 'germline_snv_indel.strelka' == self.caller:
            return CallGermlineVariantsWithStrelka(
                fq_list=self.fq_list, cram_list=self.cram_list,
                read_groups=self.read_groups, sample_names=self.sample_names,
                ref_fa_path=self.ref_fa_path,
                dbsnp_vcf_path=self.dbsnp_vcf_path,
                mills_indel_vcf_path=self.mills_indel_vcf_path,
                known_indel_vcf_path=self.known_indel_vcf_path,
                evaluation_interval_path=self.evaluation_interval_path,
                cf=self.cf, n_cpu=self.n_cpu, memory_mb=self.memory_mb,
                sh_config=self.sh_config
            )
        elif 'somatic_sv.delly' == self.caller:
            return CallStructualVariantsWithDelly(
                fq_list=self.fq_list, cram_list=self.cram_list,
                read_groups=self.read_groups, sample_names=self.sample_names,
                ref_fa_path=self.ref_fa_path,
                dbsnp_vcf_path=self.dbsnp_vcf_path,
                mills_indel_vcf_path=self.mills_indel_vcf_path,
                known_indel_vcf_path=self.known_indel_vcf_path,
                evaluation_interval_path=self.evaluation_interval_path,
                cf=self.cf, n_cpu=self.n_cpu, memory_mb=self.memory_mb,
                sh_config=self.sh_config
            )
        elif 'somatic_cnv.gatk' == self.caller:
            return CallCopyRatioSegmentsMatched(
                fq_list=self.fq_list, cram_list=self.cram_list,
                read_groups=self.read_groups, sample_names=self.sample_names,
                ref_fa_path=self.ref_fa_path,
                dbsnp_vcf_path=self.dbsnp_vcf_path,
                mills_indel_vcf_path=self.mills_indel_vcf_path,
                known_indel_vcf_path=self.known_indel_vcf_path,
                gnomad_vcf_path=self.gnomad_vcf_path,
                evaluation_interval_path=self.evaluation_interval_path,
                cnv_blacklist_path=self.cnv_blacklist_path, cf=self.cf,
                n_cpu=self.n_cpu, memory_mb=self.memory_mb,
                sh_config=self.sh_config
            )
        elif 'somatic_msi.msisensor' == self.caller:
            return ScoreMsiWithMsisensorPro(
                fq_list=self.fq_list, cram_list=self.cram_list,
                read_groups=self.read_groups, sample_names=self.sample_names,
                ref_fa_path=self.ref_fa_path,
                dbsnp_vcf_path=self.dbsnp_vcf_path,
                mills_indel_vcf_path=self.mills_indel_vcf_path,
                known_indel_vcf_path=self.known_indel_vcf_path,
                evaluation_interval_path=self.evaluation_interval_path,
                cf=self.cf, n_cpu=self.n_cpu, memory_mb=self.memory_mb,
                sh_config=self.sh_config
            )
        else:
            raise ValueError(f'invalid caller: {self.caller}')

    def output(self):
        output_files = list(
            chain.from_iterable(
                [
                    Path(self.cf['postproc_dir_path']).joinpath(a).joinpath(
                        (Path(p).stem + f'.{a}.seg.tsv') if p.endswith('.seg')
                        else (
                            Path(Path(p).stem).stem
                            + ('.norm' if self.normalize_vcf else '')
                            + ('.vcf.gz' if a == 'norm' else f'.{a}.vcf.gz')
                        )
                    )
                ] for p, a in self._generate_annotation_targets()
            )
        )
        return (
            [luigi.LocalTarget(o) for o in output_files]
            if output_files else self.input()
        )

    def _generate_annotation_targets(self):
        for i in self.input():
            p = i.path
            if p.endswith('.called.seg') and 'funcotator' in self.annotators:
                yield p, 'funcotator'
            elif p.endswith('.vcf.gz'):
                if self.normalize_vcf:
                    yield p, 'norm'
                if ('funcotator' in self.annotators
                        and not self.caller.startswith('somatic_sv.')):
                    yield p, 'funcotator'
                if 'snpeff' in self.annotators:
                    yield p, 'snpeff'

    def run(self):
        postproc_dir = Path(self.cf['postproc_dir_path'])
        norm_dir = postproc_dir.joinpath('norm')
        for p, a in self._generate_annotation_targets():
            if a == 'funcotator':
                data_src_dir_path = (
                    self.funcotator_germline_data_dir_path
                    if self.caller.startswith('germline_')
                    else self.funcotator_somatic_data_dir_path
                )
                if p.endswith('.seg'):
                    yield AnnotateSegWithFuncotateSegments(
                        input_seg_path=p, fa_path=self.ref_fa_path,
                        data_src_dir_path=data_src_dir_path,
                        ref_version=self.cf['ucsc_hg_version'],
                        dest_dir_path=str(postproc_dir.joinpath(a)),
                        gatk=self.cf['gatk'], n_cpu=self.n_cpu,
                        memory_mb=self.memory_mb, sh_config=self.sh_config
                    )
                else:
                    yield AnnotateVariantsWithFuncotator(
                        input_vcf_path=p, fa_path=self.ref_fa_path,
                        data_src_dir_path=data_src_dir_path,
                        ref_version=self.cf['ucsc_hg_version'],
                        dest_dir_path=str(postproc_dir.joinpath(a)),
                        normalize_vcf=self.normalize_vcf,
                        norm_dir_path=str(norm_dir),
                        bcftools=self.cf['bcftools'], gatk=self.cf['gatk'],
                        n_cpu=self.n_cpu, memory_mb=self.memory_mb,
                        sh_config=self.sh_config
                    )
            elif a == 'snpeff':
                yield AnnotateVariantsWithSnpeff(
                    input_vcf_path=p, fa_path=self.ref_fa_path,
                    snpeff_data_dir_path=self.snpeff_data_dir_path,
                    dest_dir_path=str(postproc_dir.joinpath(a)),
                    genome_version=self.cf['ncbi_hg_version'],
                    normalize_vcf=self.normalize_vcf,
                    norm_dir_path=str(norm_dir), bcftools=self.cf['bcftools'],
                    snpeff=self.cf['snpeff'], bgzip=self.cf['bgzip'],
                    tabix=self.cf['tabix'], n_cpu=self.n_cpu,
                    memory_mb=self.memory_mb, sh_config=self.sh_config
                )
            elif a == 'vep':
                yield AnnotateVariantsWithEnsemblVep(
                    input_vcf_path=p, fa_path=self.ref_fa_path,
                    cache_dir_path=self.vep_cache_dir_path,
                    dest_dir_path=str(postproc_dir.joinpath(a)),
                    normalize_vcf=self.normalize_vcf,
                    norm_dir_path=str(norm_dir), bcftools=self.cf['bcftools'],
                    vep=self.cf['vep'], pigz=self.cf['pigz'],
                    n_cpu=self.n_cpu, memory_mb=self.memory_mb,
                    sh_config=self.sh_config
                )
            elif a == 'norm' and not self.annotators:
                yield NormalizeVcf(
                    input_vcf_path=p, fa_path=self.ref_fa_path,
                    dest_dir_path=str(norm_dir), bcftools=self.cf['bcftools'],
                    n_cpu=self.n_cpu, memory_mb=self.memory_mb,
                    sh_config=self.sh_config
                )
        logger = logging.getLogger(__name__)
        logger.debug('Task tree:' + os.linesep + deps_tree.print_tree(self))


if __name__ == '__main__':
    luigi.run()