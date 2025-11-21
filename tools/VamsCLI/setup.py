"""Setup configuration for VamsCLI."""

from setuptools import setup, find_packages
import os

# Read the version from the version file
def get_version():
    version_file = os.path.join(os.path.dirname(__file__), 'vamscli', 'version.py')
    version_dict = {}
    with open(version_file, 'r') as f:
        exec(f.read(), version_dict)
    return version_dict['__version__']

# Read the README file
def get_long_description():
    readme_file = os.path.join(os.path.dirname(__file__), 'README.md')
    if os.path.exists(readme_file):
        with open(readme_file, 'r', encoding='utf-8') as f:
            return f.read()
    return "VamsCLI - Command Line Interface for Visual Asset Management System (VAMS)"

setup(
    name='vamscli',
    version=get_version(),
    description='Command Line Interface for Visual Asset Management System (VAMS)',
    long_description=get_long_description(),
    long_description_content_type='text/markdown',
    author='AWS Labs',
    author_email='aws-labs@amazon.com',
    url='https://github.com/awslabs/visual-asset-management-system',
    packages=find_packages(),
    include_package_data=True,
    install_requires=[
        'click>=8.3.1',
        'requests>=2.25.0',
        'boto3>=1.26.0',
        'pycognito>=2024.5.1',
        'aiohttp>=3.8.0',
        'aiofiles>=23.0.0',
        'botocore>=1.29.0',
        'geojson>=3.2.0',
        'rich>=14.2.0',
        'tqdm>=4.67.1',
        'defusedxml>=0.7.1',
    ],
    extras_require={
        'dev': [
            'pytest>=7.0.0',
            'pytest-cov>=4.0.0',
            'pytest-asyncio>=0.21.0',
            'black>=22.0.0',
            'flake8>=5.0.0',
            'mypy>=1.0.0',
        ]
    },
    entry_points={
        'console_scripts': [
            'vamscli=vamscli.main:main',
        ],
    },
    classifiers=[
        'Development Status :: 4 - Beta',
        'Intended Audience :: Developers',
        'Intended Audience :: System Administrators',
        'License :: OSI Approved :: Apache Software License',
        'Operating System :: OS Independent',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.12',
        'Programming Language :: Python :: 3.13',
        'Topic :: Software Development :: Libraries :: Python Modules',
        'Topic :: System :: Systems Administration',
        'Topic :: Utilities',
    ],
    python_requires='>=3.12',
    keywords='aws vams cli visual asset management system',
    project_urls={
        'Bug Reports': 'https://github.com/awslabs/visual-asset-management-system/issues',
        'Source': 'https://github.com/awslabs/visual-asset-management-system',
        'Documentation': 'https://github.com/awslabs/visual-asset-management-system/blob/main/README.md',
    },
)
