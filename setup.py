import setuptools
from outdated import check_outdated
from mintamazontagger import VERSION
from distutils.errors import DistutilsError


with open("README.md", "r") as fh:
    long_description = fh.read()


class CleanCommand(setuptools.Command):
    """Custom clean command to tidy up the project root."""
    user_options = []

    def initialize_options(self):
        pass

    def finalize_options(self):
        pass

    def run(self):
        import shutil
        dirs = [
            'build',
            'dist',
            'tagger-release',
            'target',
            'release_venv',
            'cache',
            'mint_amazon_tagger.egg-info',
        ]
        for tree in dirs:
            shutil.rmtree(tree, ignore_errors=True)
        import os
        from glob import glob
        globs = ('**/*.pyc', '**/*.tgz', '**/*.pyo')
        for g in globs:
            for file in glob(g, recursive=True):
                try:
                    os.remove(file)
                except OSError:
                    print("Error while deleting file: {}".format(file))


class BlockReleaseCommand(setuptools.Command):
    """Raises an error if VERSION is already present on PyPI."""
    user_options = []

    def initialize_options(self):
        pass

    def finalize_options(self):
        pass

    def run(self):
        try:
            stale, latest = check_outdated('mint-amazon-tagger', VERSION)
            raise DistutilsError(
                'Please update VERSION in __init__. '
                'Current {} PyPI latest {}'.format(VERSION, latest))
        except ValueError:
            pass


setuptools.setup(
    name="mint-amazon-tagger",
    version=VERSION,
    author="Jeff Prouty",
    author_email="jeff.prouty@gmail.com",
    description=("Fetches your Amazon order history and matching/tags your "
                 "Mint transactions"),
    keywords='amazon mint tagger transactions order history',
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/jprouty/mint-amazon-tagger",
    packages=setuptools.find_packages(),
    python_requires='>=3',
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Topic :: Office/Business :: Financial",
    ],
    # Note: this is a subset of the fbs requirements; only what's needed to
    # directly launch the gui or cli from python.
    install_requires=[
        'PyQt5',
        'mock',
        'mintapi>=1.43',
        'outdated',
        'progress',
        'range-key-dict',
        'requests',
        'readchar',
        'selenium',
        'selenium-requests',
    ],
    entry_points=dict(
        console_scripts=[
            'mint-amazon-tagger-cli=mintamazontagger.cli:main',
            'mint-amazon-tagger=mintamazontagger.main:main',
        ],
    ),
    cmdclass={
        'clean': CleanCommand,
        'block_on_version': BlockReleaseCommand,
    },
)
