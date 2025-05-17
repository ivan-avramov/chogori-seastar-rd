# Building the chogori fork
```
docker build -t chogori-builder docker/dev-ubuntu
docker run --init --rm -it -v ${PWD}:/build chogori-builder
./install-dependencies.sh
./configure.py --mode=release --prefix=/usr/local
$ ninja -C build/release install
```
