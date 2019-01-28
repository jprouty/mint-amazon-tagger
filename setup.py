import setuptools

with open("README.md", "r") as fh:
    long_description = fh.read()

setuptools.setup(
    name="mint-amazon-tagger",
    version="1.0",
    author="Jeff Prouty",
    author_email="jeff.prouty@gmail.com",
    description=("Fetches your Amazon order history and matching/tags your "
                 "Mint transactions"),
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/jprouty/mint-amazon-tagger",
    packages=setuptools.find_packages(),
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    install_requires=[
        'keyring',
        'interruptingcow',
        'mock',
        'mintapi',
        'progress',
        'requests',
        'readchar',
        'selenium',
        'selenium-requests',
    ],
    entry_points=dict(
        console_scripts=[
            'mint-amazon-tagger = mint-amazon-tagger.fetch_:main',
            'mint-amazon-tagger_tagonly = mint-amazon-tagger.tagger:main',
        ],
    ),
)
