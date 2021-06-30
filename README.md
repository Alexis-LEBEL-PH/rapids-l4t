# RAPIDS-L4T

This repository is dedicated to maintaining a "Feedstock"-like set of build scripts to create necessary libraries for building the NVIDIA RAPIDS ecosystem for the NVIDIA Jetson ecosystem of products.  Starting with v0.14.0 the RAPIDS ecosystem has made changes necessary to allow building of products for aarch64 architecture.  Official support remains for CUDA CC 6.+, meaning the Jetson Nano is not officially supported.  All RAPIDS libraries require a TX2, AGX Xavier, or Xavier NX product in order to run.

## Building Prerequisites
1. __cudatoolkit__
The L4T distributions rely on a an Extractor that moves the necessary pre-built binaries out of a .run archive and saves them into the proper ${PREFIX} folder.  However, the Linux 4 Tegra ecosystem distributes packages for the Ubuntu package manager via .deb archives.  Therefor, the cudatoolkit-feedstock is modified to un-archive the compiled package from the NVIDIA package manager archive and extract the same necessary files.
```bash
conda build cudatoolkit-feedstock/
```
2. __nvcc__
The nvcc feedstock is largely the same, the only modification needed is an alternative .ci_support file that includes
```json
target_platform:
- linux-aarch64
```
Generate this script and build the nvcc-feedstock with the following commands
```bash
cd nvcc-feedstock
conda build -m .ci_support/linux_cuda_compiler_version10.2target_platformlinux-aarch64.yaml recipe/
```
3. __dlpack__
RAPIDS v0.19.0 used dlpack==0.3, and conda-forge does not have an aarch64 version of dlpack 0.3 at the time of this creation, so the dlpack-feedstock is slightly modified to build 0.3 using the modern `.ci_support/linux_aarch64_.yaml` file.
Build dlpack locally with the following commands
```bash
cd dlpack-feedstock
conda build -m .ci_support/linux_aarch64_.yaml recipe/
cd ..
```

4. __arrow-cpp__
All of the pull requests necessary to build arrow-cpp for aarch64 and CUDA were accepted into the repository.  However, because there is no way to integrate and test a Jetson device into the conda-forge CI/CD pipeline, there was no .ci_support file generated to automate the process and incorporate into conda-forge, so here we need a .ci_support file specific to both cuda and target_platform=linux-aarch64.  Also, the build-pyarrow.sh file needs to be modified to not compile Gandiva.

```bash
cd arrow-cpp-feedstock
conda build -m .ci_support/linux_aarch64_cuda_compiler_version10.2numpy1.17python3.7.____cpython.yaml recipe/
```

5. __cupy__
CUPY needs to be compiled from source directly on the Jetson so that it can grab the Jetson specific libraries it requires.

```bash
git clone --branch v8.6.0 --recurse-submodules https://github.com/cupy/cupy.git
cd cupy
python setup.py bdist_conda
```

## Building cuDF
Between what is already available on conda-forge and what we just built, we now have everything we need for RMM and cuDF.  We can build version 0.19.0 as the last version that included CUDA 10.2.89 compatibility.  Until JetPack upgrades to CUDA 11, RAPIDS-L4T will be held back at 0.19.0 support.

```bash
git clone --branch v0.19.0 --single-branch --recurse-submodules https://github.com/rapidsai/rmm.git
cd rmm/conda/recipes
PARALLEL_LEVEL=8 CUDA_VERSION=10.2 conda build librmm
PARALLEL_LEVEL=8 CUDA_VERSION=10.2 conda build rmm
```

```bash
git clone --branch v0.19.0 --single-branch --recurse-submodules https://github.com/rapidsai/cudf.git
cd cudf/conda/recipes
PARALLEL_LEVEL=2 CUDA_VERSION=10.2 conda build libcudf
PARALLEL_LEVEL=2 CUDA_VERSION=10.2 conda build cudf
```
<sub> We build with parallel set to 2 because sort.cu takes an enormous amount of memory to compile.  With a 32 gig AGX Xavier device we can successfully compile with Parallel_Level=2.  If you are building with a Xavier NX or a TX2-NX you will need to create a linux swapfile >20GB on an external storage device.</sub>
