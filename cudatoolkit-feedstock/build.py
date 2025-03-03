from __future__ import print_function
import fnmatch
import platform
import hashlib
import os
import sys
import shutil
import tarfile
import urllib.parse as urlparse
import yaml

from contextlib import contextmanager
from pathlib import Path
from subprocess import check_call
from tempfile import TemporaryDirectory as tempdir

import requests
from tqdm import tqdm
import os

def download_from_url(url, dst):
    """
    @param: url to download file
    @param: dst place to put the file
    """
    file_size = requests.head(url).headers.get("content-length",None)
    if file_size: file_size = int(file_size)
    if os.path.exists(dst):
        first_byte = os.path.getsize(dst)
    else:
        first_byte = 0
    if file_size and first_byte >= file_size:
        return file_size

    pbar = None
    if file_size:
        header = {"Range": "bytes=%s-%s" % (first_byte, file_size)}
        pbar = tqdm(
            total=file_size, initial=first_byte,
            unit='B', unit_scale=True, desc=url.split('/')[-1])

    req = requests.get(url, stream=True)
    
    with(open(dst, 'ab')) as f:
        for chunk in req.iter_content(chunk_size=1024):
            if chunk:
                f.write(chunk)
                if file_size: 
                    pbar.update(1024)
        if pbar: pbar.close()
    return file_size

def md5(fname):
    hash_md5 = hashlib.md5()
    with open(fname, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_md5.update(chunk)
    return hash_md5.hexdigest()

# The config dictionary looks like:
# config[cuda_version(s)...]
#
# and for each cuda_version the keys:
# base_url the base url for all downloads
# patch_url_ext the extra path needed to reach the patch directory from base_url
# installers_url_ext the extra path needed to reach the local installers directory
# md5_url the url for checksums
# cuda_libraries the shared libraries to copy in
# cuda_static_libraries the static libraries to copy in
# libdevice_versions the library device versions supported (.bc files)
# linux the linux platform config (see below)
# windows the windows platform config (see below)
#
# For each of the two platform specific dictionaries, linux and windows
# a dictionary containing keys:
# blob the name of the downloaded file, for linux this is the .run file
# patches a list of the patch files for the blob, they are applied in order
# cuda_lib_fmt string format for the cuda libraries
# nvvm_lib_fmt string format for the nvvm libraries
# libdevice_lib_fmt string format for the libdevice.compute bitcode file
#
# To accommodate nvtoolsext not being present as a DLL in the installer PE32s on windows,
# the windows variant of this script supports assembly directly from a pre-installed
# CUDA toolkit. The environment variable "NVTOOLSEXT_INSTALL_PATH" can be set to the
# installation path of the CUDA toolkit's NvToolsExt location (this is not the user
# defined install directory) and the DLL will be taken from that location.


#######################
### CUDA 10.2 setup ###
#######################

maj_min = '10.2'
config = {}
config['base_url'] = f"http://developer.download.nvidia.com/compute/cuda/{maj_min}/Prod/"
config['installers_url_ext'] = 'local_installers/'
config['patch_url_ext'] = ''
config['md5_url'] = f"{config['base_url']}/docs/sidebar/md5sum.txt"
config['cuda_libraries'] = [
    'cublas',
    'cublasLt',
    'cudart',
    'cufft',
    'cufftw',
    'curand',
    'cusolver',
    'cusparse',
    'nppc',
    'nppial',
    'nppicc',
    'nppicom',
    'nppidei',
    'nppif',
    'nppig',
    'nppim',
    'nppist',
    'nppisu',
    'nppitc',
    'npps',
    'nvToolsExt',
    'nvblas',
    'nvgraph',
    'nvrtc',
    'nvrtc-builtins',
]
config['cuda_static_libraries'] = [
    'cudadevrt'
]
# nvjpeg is only available on linux
if sys.platform.startswith('linux') and platform.machine() != 'aarch64':
    config['cuda_libraries'].append('nvjpeg')
config['libdevice_versions'] = ['10']

config['linux'] = {
    'blob': 'cuda_10.2.89_440.33.01_rhel6.run',
    'ppc64le_blob': 'cuda_10.2.89_440.33.01_linux_ppc64le.run',
    'embedded_blob': 'cuda-linux.10.2.89-27506705.run',
    'ppc64le_embedded_blob': None,
    'patches': [],
    # need globs to handle symlinks
    'cuda_lib_fmt': 'lib{0}.so*',
    'cuda_static_lib_fmt': 'lib{0}.a',
    'nvtoolsext_fmt': 'lib{0}.so*',
    'nvvm_lib_fmt': 'lib{0}.so*',
    'libdevice_lib_fmt': 'libdevice.{0}.bc'
}

config['linux-aarch64'] = {
    'blob': 'l4t_cuda.yaml',
    'embedded_blob': None,
    'patches': [],
    # need globs to handle symlinks
    'cuda_lib_fmt': 'lib{0}.so*',
    'cuda_static_lib_fmt': 'lib{0}.a',
    'nvtoolsext_fmt': 'lib{0}.so*',
    'nvvm_lib_fmt': 'lib{0}.so*',
    'libdevice_lib_fmt': 'libdevice.{0}.bc'
}


config['windows'] = {'blob': 'cuda_10.2.89_441.22_windows.exe',
                   'patches': [],
                   'cuda_lib_fmt': '{0}64_10*.dll',
                   'cuda_static_lib_fmt': '{0}.lib',
                   'nvtoolsext_fmt': '{0}64_1.dll',
                   'nvvm_lib_fmt': '{0}64_33_0.dll',
                   'libdevice_lib_fmt': 'libdevice.{0}.bc',
                   'NvToolsExtPath' :
                       os.path.join('c:' + os.sep, 'Program Files',
                                    'NVIDIA Corporation', 'NVToolsExt', 'bin')
                   }



class Extractor(object):
    """Extractor base class, platform specific extractors should inherit
    from this class.
    """

    libdir = {'linux': 'lib',
              'linux-aarch64': 'lib',
              'windows': 'Library/bin'}

    def __init__(self, version, ver_config, plt_config):
        """Initialise an instance:
        Arguments:
          version - CUDA version string
          ver_config - the configuration for this CUDA version
          plt_config - the configuration for this platform
        """
        self.config_version = version
        self.md5_url = ver_config['md5_url']
        self.base_url = ver_config['base_url']
        self.patch_url_ext = ver_config['patch_url_ext']
        self.installers_url_ext = ver_config['installers_url_ext']
        self.cuda_libraries = ver_config['cuda_libraries']
        self.cuda_static_libraries = ver_config['cuda_static_libraries']
        self.libdevice_versions = ver_config['libdevice_versions']
        self.config_blob = plt_config['blob']
        self.embedded_blob = plt_config.get('embedded_blob', None)
        self.cuda_lib_fmt = plt_config['cuda_lib_fmt']
        self.cuda_static_lib_fmt = plt_config['cuda_static_lib_fmt']
        self.nvtoolsext_fmt = plt_config.get('nvtoolsext_fmt')
        self.nvvm_lib_fmt = plt_config['nvvm_lib_fmt']
        self.libdevice_lib_fmt = plt_config['libdevice_lib_fmt']
        self.patches = plt_config['patches']
        self.nvtoolsextpath = plt_config.get('NvToolsExtPath')
        self.config = {'version': version, **ver_config}
        self.prefix = os.environ['PREFIX']
        self.src_dir = os.environ['SRC_DIR']
        self.output_dir = os.path.join(self.prefix, self.libdir[getplatform()])
        self.symlinks = 'linux' in getplatform()
        self.debug_install_path = os.environ.get('DEBUG_INSTALLER_PATH')

        try:
            os.mkdir(self.output_dir)
        except FileExistsError:
            pass

    def download_blobs(self):
        """Downloads the binary blobs to the $SRC_DIR
        """
        dl_url = urlparse.urljoin(self.base_url, self.installers_url_ext)
        dl_url = urlparse.urljoin(dl_url, self.config_blob)
        dl_path = os.path.join(self.src_dir, self.config_blob)
        if not self.debug_install_path:
            print("downloading %s to %s" % (dl_url, dl_path))
            download_from_url(dl_url, dl_path)
        else:
            existing_file = os.path.join(self.debug_install_path, self.config_blob)
            print("DEBUG: copying %s to %s" % (existing_file, dl_path))
            shutil.copy(existing_file, dl_path)

        for p in self.patches:
            dl_url = urlparse.urljoin(self.base_url, self.patch_url_ext)
            dl_url = urlparse.urljoin(dl_url, p)
            dl_path = os.path.join(self.src_dir, p)
            if not self.debug_install_path:
                print("downloading %s to %s" % (dl_url, dl_path))
                download_from_url(dl_url, dl_path)
            else:
                existing_file = os.path.join(self.debug_install_path, p)
                print("DEBUG: copying %s to %s" % (existing_file, dl_path))
                shutil.copy(existing_file, dl_path)

    def check_md5(self):
        """Checks the md5sums of the downloaded binaries
        """
        md5file = self.md5_url.split('/')[-1]
        path = os.path.join(self.src_dir, md5file)
        download_from_url(self.md5_url, path)

        # compute hash of blob
        blob_path = os.path.join(self.src_dir, self.config_blob)
        md5sum = md5(blob_path)

        # get checksums
        with open(md5file, 'r') as f:
            checksums = [x.strip().split() for x in f.read().splitlines() if x]

        # check md5 and filename match up
        check_dict = {x[0]: x[1] for x in checksums}
        assert check_dict[md5sum].startswith(self.config_blob[:-7])

    def copy(self, *args):
        """The method to copy extracted files into the conda package platform
        specific directory. Platform specific extractors must implement.
        """
        raise RuntimeError('Must implement')

    def extract(self, *args):
        """The method to extract files from the cuda binary blobs.
        Platform specific extractors must implement.
        """
        raise RuntimeError('Must implement')

    def get_paths(self, libraries, dirpath, template):
        """Gets the paths to the various cuda libraries and bc files
        """
        pathlist = []
        for libname in libraries:
            filename = template.format(libname)
            paths = fnmatch.filter(os.listdir(dirpath), filename)
            if not paths:
                msg = ("Cannot find item: %s, looked for %s" %
                       (libname, filename))
                raise RuntimeError(msg)
            if (not self.symlinks) and (len(paths) != 1):
                msg = ("Aliasing present for item: %s, looked for %s" %
                       (libname, filename))
                msg += ". Found: \n"
                msg += ', \n'.join([str(x) for x in paths])
                raise RuntimeError(msg)
            pathsforlib = []
            for path in paths:
                tmppath = os.path.join(dirpath, path)
                assert os.path.isfile(tmppath), 'missing {0}'.format(tmppath)
                pathsforlib.append(tmppath)
            if self.symlinks: # deal with symlinked items
                # get all DSOs
                concrete_dsos = [x for x in pathsforlib
                                 if not os.path.islink(x)]
                # find the most recent library version by name
                target_library = max(concrete_dsos)
                # remove this from the list of concrete_dsos
                # all that remains are DSOs that are not wanted
                concrete_dsos.remove(target_library)
                # drop the unwanted DSOs from the paths
                [pathsforlib.remove(x) for x in concrete_dsos]
            pathlist.extend(pathsforlib)
        return pathlist

    def copy_files(self, cuda_lib_dir, nvvm_lib_dir, libdevice_lib_dir):
        """Copies the various cuda libraries and bc files to the output_dir
        """
        filepaths = []
        # nvToolsExt is different to the rest of the cuda libraries,
        # it follows a different naming convention, this accommodates...
        cudalibs = [x for x in self.cuda_libraries if x != 'nvToolsExt']
        filepaths += self.get_paths(cudalibs, cuda_lib_dir, self.cuda_lib_fmt)
        if 'nvToolsExt' in self.cuda_libraries:
            filepaths += self.get_paths(['nvToolsExt'], cuda_lib_dir,
                                        self.nvtoolsext_fmt)
        filepaths += self.get_paths(self.cuda_static_libraries, cuda_lib_dir,
                                    self.cuda_static_lib_fmt)
        filepaths += self.get_paths(['nvvm'], nvvm_lib_dir, self.nvvm_lib_fmt)
        filepaths += self.get_paths(self.libdevice_versions, libdevice_lib_dir,
                                    self.libdevice_lib_fmt)

        for fn in filepaths:
            if os.path.islink(fn):
                # replicate symlinks
                symlinktarget = os.readlink(fn)
                targetname = os.path.basename(fn)
                symlink = os.path.join(self.output_dir, targetname)
                print('linking %s to %s' % (symlinktarget, symlink))
                os.symlink(symlinktarget, symlink)
            else:
                print('copying %s to %s' % (fn, self.output_dir))
                shutil.copy(fn, self.output_dir)

    def dump_config(self):
        """Dumps the config dictionary into the output directory
        """
        dumpfile = os.path.join(self.output_dir, 'cudatoolkit_config.yaml')
        with open(dumpfile, 'w') as f:
            yaml.dump(self.config, f, default_flow_style=False)


class WindowsExtractor(Extractor):
    """The windows extractor
    """

    def copy(self, *args):
        store, = args
        self.copy_files(
            cuda_lib_dir=store,
            nvvm_lib_dir=store,
            libdevice_lib_dir=store)

    def extract(self):
        runfile = self.config_blob
        patches = self.patches
        try:
            with tempdir() as tmpd:
                extract_name = '__extracted'
                extractdir = os.path.join(tmpd, extract_name)
                os.mkdir(extract_name)

                check_call(['7za', 'x', '-o%s' %
                            extractdir, os.path.join(self.src_dir, runfile)])
                for p in patches:
                    check_call(['7za', 'x', '-aoa', '-o%s' %
                                extractdir, os.path.join(self.src_dir, p)])

                nvt_path = os.environ.get('NVTOOLSEXT_INSTALL_PATH', self.nvtoolsextpath)
                print("NvToolsExt path: %s" % nvt_path)
                if nvt_path is not None:
                    if not Path(nvt_path).is_dir():
                        msg = ("NVTOOLSEXT_INSTALL_PATH is invalid "
                                "or inaccessible.")
                        raise ValueError(msg)

                # fetch all the dlls into DLLs
                store_name = 'DLLs'
                store = os.path.join(tmpd, store_name)
                os.mkdir(store)
                for path, dirs, files in os.walk(extractdir):
                    if 'jre' not in path and 'GFExperience' not in path:  # don't get jre or GFExperience dlls
                        for filename in fnmatch.filter(files, "*.dll"):
                            if not Path(os.path.join(
                                    store, filename)).is_file():
                                shutil.copy(
                                    os.path.join(path, filename),
                                    store)
                        for filename in fnmatch.filter(files, "*.lib"):
                            if not Path(os.path.join(
                                    store, filename)).is_file():
                                shutil.copy(
                                    os.path.join(path, filename),
                                    store)
                        for filename in fnmatch.filter(files, "*.bc"):
                            if not Path(os.path.join(
                                    store, filename)).is_file():
                                shutil.copy(
                                    os.path.join(path, filename),
                                    store)
                if nvt_path is not None:
                    for path, dirs, files in os.walk(nvt_path):
                        for filename in fnmatch.filter(files, "*.dll"):
                            if not Path(os.path.join(
                                    store, filename)).is_file():
                                shutil.copy(
                                    os.path.join(path, filename),
                                    store)
                self.copy(store)
        except PermissionError:
            # TODO: fix this
            # cuda 8 has files that refuse to delete, figure out perm changes
            # needed and apply them above, tempdir context exit fails to rmtree
            pass


class LinuxExtractor(Extractor):
    """The linux extractor
    """

    def __init__(self, version, ver_config, plt_config):
        if platform.machine() == 'ppc64le':
            if plt_config.get('ppc64le_blob') is not None:
                plt_config['blob'] = plt_config['ppc64le_blob']
            else:
                raise RuntimeError('ppc64le not supported for %s' % version)
            plt_config['embedded_blob'] = plt_config['ppc64le_embedded_blob']

        super(LinuxExtractor, self).__init__(version, ver_config, plt_config)

    def copy(self, *args):
        basepath = args[0]
        if self.embedded_blob is not None:
            cudapath=''
        else:
            cudapath='cuda-toolkit'
        self.copy_files(
            cuda_lib_dir=os.path.join(
                basepath, cudapath, 'lib64'), nvvm_lib_dir=os.path.join(
                basepath, cudapath, 'nvvm', 'lib64'), libdevice_lib_dir=os.path.join(
                basepath, cudapath, 'nvvm', 'libdevice'))

    def extract(self):
        runfile = self.config_blob
        patches = self.patches
        os.chmod(runfile, 0o777)
        with tempdir() as tmpd:
            if self.embedded_blob is not None:
                with tempdir() as tmpd2:
                    cmd = [os.path.join(self.src_dir, runfile),
                           '--extract=%s' % (tmpd2, ), '--nox11', '--silent']
                    check_call(cmd)
                    # extract the embedded blob
                    cmd = [os.path.join(tmpd2, self.embedded_blob),
                           '-prefix', tmpd, '-noprompt', '--nox11']
                    check_call(cmd)
            else:
                # Nvidia's RHEL7 based runfiles don't use embedded runfiles
                # Once the toolkit is extracted, it ends up in a directory called "cuda-toolkit'
                # the --extract runfile command is used because letting the runfile do an "install" 
                # results in attempted installs of .pc and doc files into standard Linux locations, 
                # which is not what we want.
                # The "--override" runfile command to disable the compiler check since we are not
                # installing the driver here.

                cmd = [os.path.join(self.src_dir, runfile),
                       '--extract=%s' % (tmpd), '--toolkit', '--silent', '--override']
                check_call(cmd)
            for p in patches:
                os.chmod(p, 0o777)
                cmd = [os.path.join(self.src_dir, p),
                            '--installdir', tmpd, '--accept-eula', '--silent']
                check_call(cmd)
            self.copy(tmpd)

class DebExtractor(Extractor):
    '''
    Extract all of the libraries from a Debian repository file
    '''
    def __init__(self, version, ver_config, plt_config):

        super(DebExtractor, self).__init__(version, ver_config, plt_config)


    def download_blobs(self):
        # Need to overwrite this super, for now a debfile is passed through the
        # command line, in the future the debfile may be downloaded
        # directly from the package manager
        print("Extracting download sources from {}".format(self.config_blob))

        with open(self.config_blob,'r') as thefile:
            deb_sources = yaml.safe_load(thefile)

        try:
            os.mkdir(os.path.join(self.src_dir,'deb_archives'))
        except FileExistsError:
            pass
        
        for package in deb_sources.keys():
            if deb_sources[package]['link']:
                dl_url = deb_sources[package]['link']
                dl_path = os.path.join(self.src_dir,'deb_archives', deb_sources[package]['name'])
                print("downloading %s to %s" % (dl_url, dl_path))
                download_from_url(dl_url, dl_path)
        return

    def check_md5(self):
        # Need to overwrite this super also, debfile md5s are included in
        # download yaml created by download_links.py
        
        with open(self.config_blob,'r') as thefile:
            deb_sources = yaml.safe_load(thefile)

        for package in deb_sources.keys():
            if deb_sources[package]['md5']:
                dl_path = os.path.join(self.src_dir,'deb_archives', deb_sources[package]['name'])
                md5sum = md5(dl_path)
                assert md5sum.startswith(deb_sources[package]['md5'][:-7])
                

        return

    def copy(self,*args):
        # Need a method to move CUDA files from the extracted archive to
        # the Anaconda lib environment

        # assemble a list of all of the binary files extracted
        # from the archive
        basepath = args[0]

        sos = {}
        for path, dirs, files in os.walk(basepath):
            for filename in files:
                if '.so' in filename and '/doc/' not in path:
                    if filename not in sos.keys(): sos[filename] = os.path.join(path,filename)


        self.copy_files(sos)

    def get_paths(self,libraries, file_dict, template):
        # An override of the runfile get_paths that work for traversing
        # an entire directory, not just looking in one folder
        pathlist = []
        for libname in libraries:
            filename = template.format(libname)
            paths = fnmatch.filter(file_dict.keys(), filename)

            if not paths:
                # Paths was empty for this library, we need this library
                msg = ("Cannot find item: %s, looked for %s" %
                       (libname, filename))
                raise RuntimeError(msg)

            if (not self.symlinks) and (len(paths) != 1):
                msg = ("Aliasing present for item: %s, looked for %s" %
                       (libname, filename))
                msg += ". Found: \n"
                msg += ', \n'.join([str(x) for x in paths])
                raise RuntimeError(msg)

            pathsforlib = []
            for path in paths:
                tmppath = file_dict[path]
                assert os.path.isfile(tmppath), 'missing {0}'.format(tmppath)
                pathsforlib.append(tmppath)
            
            if self.symlinks: # deal with symlinked items
                # get all DSOs
                concrete_dsos = [x for x in pathsforlib
                                 if not os.path.islink(x)]
                # find the most recent library version by name
                target_library = max(concrete_dsos)
                # remove this from the list of concrete_dsos
                # all that remains are DSOs that are not wanted
                concrete_dsos.remove(target_library)
                # drop the unwanted DSOs from the paths
                [pathsforlib.remove(x) for x in concrete_dsos]

            pathlist.extend(pathsforlib)
        return pathlist


    def copy_files(self, file_dict):

        # previously we've assembled a dictionary of all of the binary
        # files and their filepaths, go through and extract the libraries
        # and handle the symbolic links
        cudalibs =  [x for x in self.cuda_libraries]
        filepaths = self.get_paths(cudalibs, file_dict,  self.cuda_lib_fmt)

        for fn in filepaths:
            if os.path.islink(fn):
                # replicate symlinks
                symlinktarget = os.readlink(fn)
                targetname = os.path.basename(fn)
                symlink = os.path.join(self.output_dir, targetname)
                print('linking %s to %s' % (symlinktarget, symlink))
                os.symlink(symlinktarget, symlink)
            else:
                print('copying %s to %s' % (fn, self.output_dir))
                shutil.copy(fn, self.output_dir)

            


    def extract(self):

        # Use dpkg to extract the libraries from a CUDA deb file

        with tempdir() as tmpd:

            extractdir = os.path.join(tmpd,"__extracted")

            # walk the extraction looking for more deb files embedded
            for path, dirs, files in os.walk(self.src_dir):
                for filename in files:
                    if filename.lower().endswith('.deb'):
                        cmd = ['dpkg',
                                 '-x',
                                os.path.join(path,filename),
                                extractdir]
                        check_call(cmd)

            self.copy(tmpd)

    

        

def getplatform():

    plt = sys.platform
    if plt.startswith('linux'):
        if platform.machine() != 'aarch64':
            return 'linux'
        else:
            return 'linux-aarch64'
    elif plt.startswith('win'):
        return 'windows'
    else:
        raise RuntimeError('Unknown platform')

dispatcher = {'linux': LinuxExtractor, 
              'linux-aarch64':DebExtractor,
              'windows': WindowsExtractor}


def _main():
    print("Running build")

    # package version decl must match cuda release version
    config_version = os.environ['PKG_VERSION']
    # keep only the major.minor version (10.0) if micro (10.0.130) is present
    major_minor, micro = config_version.rsplit('.', 1)
    if '.' in major_minor:
        config_version = major_minor

    # get an extractor
    plat = getplatform()
    extractor_impl = dispatcher[plat]
        
    extractor = extractor_impl(config_version, config, config[plat])

    # download binaries
    extractor.download_blobs()

    # check md5sum
    extractor.check_md5()

    # extract
    extractor.extract()

    # dump config
    extractor.dump_config()

if __name__ == "__main__":
    _main()
