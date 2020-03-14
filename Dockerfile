FROM ubuntu:latest AS builder

ENV DEBIAN_FRONTEND noninteractive

COPY --from=dceoy/gatk:latest /usr/local /usr/local
COPY --from=dceoy/gatk:latest /opt/conda /opt/conda
COPY --from=dceoy/samtools:latest /usr/local/src/samtools /usr/local/src/samtools
COPY --from=dceoy/bwa:latest /usr/local/src/bwa /usr/local/src/bwa
COPY --from=dceoy/trim_galore:latest /usr/local/src/FastQC /usr/local/src/FastQC
COPY --from=dceoy/trim_galore:latest /usr/local/src/TrimGalore /usr/local/src/TrimGalore
COPY --from=dceoy/bcftools:latest /usr/local/src/bcftools /usr/local/src/bcftools
COPY --from=dceoy/manta:latest /opt/manta /opt/manta
COPY --from=dceoy/strelka:latest /opt/strelka /opt/strelka
COPY --from=dceoy/cnvnator:latest /opt/root /opt/root
COPY --from=dceoy/cnvnator:latest /usr/local/src/cnvnator /usr/local/src/cnvnator
COPY --from=dceoy/delly:latest /usr/local/bin/delly /usr/local/bin/delly
COPY --from=dceoy/msisensor:latest /usr/local/bin/msisensor /usr/local/bin/msisensor
COPY --from=dceoy/smoove:latest /usr/local/src/lumpy-sv /usr/local/src/lumpy-sv
COPY --from=dceoy/smoove:latest /usr/local/lib/libdeflate.a /usr/local/lib/libdeflate.a
COPY --from=dceoy/smoove:latest /usr/local/include/libdeflate.h /usr/local/include/libdeflate.h
COPY --from=dceoy/smoove:latest /usr/local/bin/batchit /usr/local/bin/batchit
COPY --from=dceoy/smoove:latest /usr/local/bin/mosdepth /usr/local/bin/mosdepth
COPY --from=dceoy/smoove:latest /usr/local/bin/duphold /usr/local/bin/duphold
COPY --from=dceoy/smoove:latest /usr/local/bin/gsort /usr/local/bin/gsort
COPY --from=dceoy/smoove:latest /usr/local/bin/bpbio /usr/local/bin/bpbio
COPY --from=dceoy/smoove:latest /usr/local/bin/gargs /usr/local/bin/gargs
COPY --from=dceoy/smoove:latest /usr/local/bin/goleft /usr/local/bin/goleft
COPY --from=dceoy/smoove:latest /usr/local/bin/smoove /usr/local/bin/smoove
ADD https://bootstrap.pypa.io/get-pip.py /tmp/get-pip.py
ADD . /tmp/vcline

RUN set -e \
      && apt-get -y update \
      && apt-get -y dist-upgrade \
      && apt-get -y install --no-install-recommends --no-install-suggests \
        gcc libbz2-dev libc-dev libcurl4-gnutls-dev libgsl-dev libperl-dev \
        liblzma-dev libssl-dev libz-dev make \
      && apt-get -y autoremove \
      && apt-get clean \
      && rm -rf /var/lib/apt/lists/*

RUN set -e \
      && /opt/conda/bin/conda update -n base -c defaults conda \
      && /opt/conda/bin/conda config --add channels defaults \
      && /opt/conda/bin/conda config --add channels bioconda \
      && /opt/conda/bin/conda config --add channels conda-forge \
      && /opt/conda/bin/conda install bicseq2-norm bicseq2-seg \
      && /opt/conda/bin/conda create -n smoove-env \
        python=2.7 awscli numpy scipy cython pysam toolshed pyvcf pyfaidx \
        cyvcf2 svtyper svtools \
      && /opt/conda/bin/conda clean -ya \
      && rm -rf /root/.cache/pip

RUN set -e \
      && find \
        /usr/local/src/bwa /usr/local/src/FastQC /usr/local/src/TrimGalore \
        /usr/local/src/cnvnator/src /usr/local/src/lumpy-sv/bin \
        -maxdepth 1 -type f -executable -exec ln -s {} /usr/local/bin \; \
      && cd /usr/local/src/samtools/htslib-* \
      && make install \
      && cd /usr/local/src/samtools \
      && make install \
      && cd /usr/local/src/bcftools \
      && make install

RUN set -e \
      && /opt/conda/bin/python3 /tmp/get-pip.py \
      && /opt/conda/bin/python3 -m pip install -U --no-cache-dir \
        cutadapt pip /tmp/vcline \
      && rm -f /tmp/get-pip.py

FROM ubuntu:latest

ENV DEBIAN_FRONTEND noninteractive

COPY --from=builder /usr/local /usr/local
COPY --from=builder /opt /opt

RUN set -e \
      && ln -sf /bin/bash /bin/sh

RUN set -e \
      && apt-get -y update \
      && apt-get -y dist-upgrade \
      && apt-get -y install --no-install-recommends --no-install-suggests \
        apt-transport-https apt-utils ca-certificates curl openjdk-8-jre \
        libcurl3-gnutls libgsl23 libncurses5 pbzip2 perl pigz python r-base \
        wget \
      && apt-get -y autoremove \
      && apt-get clean \
      && rm -rf /var/lib/apt/lists/*

RUN set -e \
      && ln -s /opt/conda/etc/profile.d/conda.sh /etc/profile.d/conda.sh \
      && echo '. /opt/conda/etc/profile.d/conda.sh' >> ~/.bashrc \
      && echo 'source activate gatk' >> /usr/local/src/gatk/gatkenv.rc \
      && echo 'source /usr/local/src/gatk/gatk-completion.sh' \
        >> /usr/local/src/gatk/gatkenv.rc

ENV ROOTSYS /opt/root
ENV PYTHONPATH ${ROOTSYS}/lib:/opt/manta/lib/python:/opt/strelka/lib/python:${PYTHONPATH}
ENV PATH ${PATH}:/opt/conda/envs/gatk/bin:/opt/conda/bin:/opt/conda/envs/smoove-env/bin:/opt/manta/bin:/opt/strelka/bin
ENV LD_LIBRARY_PATH ${ROOTSYS}/lib:/usr/local/src/cnvnator/yeppp-1.0.0/binaries/linux/x86_64:${LD_LIBRARY_PATH}

ENTRYPOINT ["/opt/conda/bin/vcline"]
