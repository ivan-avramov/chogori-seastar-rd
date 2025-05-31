FROM ubuntu:25.04

ENV DEBIAN_FRONTEND=noninteractive
RUN apt update && apt upgrade -y && apt install -y \
    apt-transport-https \
    pkg-config \
    ca-certificates \
    cmake \
    curl \
    gdb \
    git \
    valgrind \
    software-properties-common \
    vim \
    bash \
    ruby \
    python3 \
    python3-pip \
    ruby-dev \
    rubygems \
    build-essential \
    openjdk-8-jdk \
    rpm \
    scons \
    git \
    iproute2 \
    telnet \
    sed;

VOLUME [ "/build" ]
WORKDIR /build

CMD ["/bin/bash"]
