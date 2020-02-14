#!/usr/bin/env python

import logging
import os
import re
import subprocess
from pathlib import Path

import luigi

from ..cli.util import print_log
from .base import ShellTask


class WriteAfOnlyVCF(ShellTask):
    src_path = luigi.Parameter(default='')
    src_url = luigi.Parameter(default='')
    dest_dir_path = luigi.Parameter(default='.')
    curl = luigi.Parameter(default='curl')
    sed = luigi.Parameter(default='sed')
    bgzip = luigi.Parameter(default='bgzip')
    n_cpu = luigi.IntParameter(default=1)

    def output(self):
        return luigi.LocalTarget(
            str(
                Path(self.dest_dir_path).joinpath(
                    Path(Path(self.src_path or self.src_url).stem).stem
                    + '.af-only.vcf.gz'
                ).resolve()
            )
        )

    def run(self):
        run_id = Path(Path(self.src_url).stem).stem
        print_log(f'Write AF-only VCF:\t{run_id}')
        self.setup_shell(
            commands=[self.curl, self.bgzip], cwd=self.dest_dir_path,
            quiet=False
        )
        dest_path = self.output().path
        _write_af_only_vcf_bgz(
            src_path=self.src_path, src_url=self.src_url, dest_path=dest_path,
            curl=self.curl, bgzip=self.bgzip, n_cpu=self.n_cpu,
            cwd=self.dest_dir_path
        )


def _write_af_only_vcf_bgz(src_path=None, src_url=None, dest_path=None,
                           curl='curl', bgzip='bgzip', n_cpu=1, shell=True,
                           executable='/bin/bash', **kwargs):
    assert bool(src_url or src_path), 'src_path or src_url is required.'
    assert bool(dest_path), 'dest_path is required.'
    logger = logging.getLogger(__name__)
    search_regex = re.compile('\tPASS\t.*[\t;]AF=[^;]')
    sub_regexes = [
        re.compile('(\t[^\t]*;|\t)(AF=[0-9]*\\.[e0-9+-]*)[^\t\r\n]*'), '\t\\2'
    ]
    args0 = (
        f'{bgzip} -dc {src_path}' if src_path else
        f'{curl} -LS {src_url} | {bgzip} -dc -'
    )
    args1 = f'{bgzip} -@ {n_cpu} -c > {dest_path}'
    logger.info(f'`{args0}` -> (extract AF using Python) -> `{args1}`')
    popen_kwargs = {'shell': shell, 'executable': executable, **kwargs}
    logger.debug(f'popen_kwargs:\t{popen_kwargs}')
    with subprocess.Popen(args=args1, stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          **popen_kwargs) as p1:
        with subprocess.Popen(args=args0, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, **popen_kwargs) as p0:
            for line in p0.stdout:
                s = line.decode('utf-8')
                if s.startswith('#'):
                    p1.stdin.write(line)
                    p1.stdin.flush()
                elif search_regex.search(s):
                    p1.stdin.write(re.sub(*sub_regexes, s).encode('utf-8'))
                    p1.stdin.flush()
            p1.stdin.close()
            for p in [p0, p1]:
                outs, errs = p.communicate()
                if p.returncode != 0:
                    logger.error(
                        f'STDERR from subprocess `{p.args}`:'
                        + os.linesep + errs.decode('utf-8')
                    )
                    raise subprocess.CalledProcessError(
                        returncode=p.returncode, cmd=p.args, output=outs,
                        stderr=errs
                    )
    assert Path(dest_path).is_file()


if __name__ == '__main__':
    luigi.run()