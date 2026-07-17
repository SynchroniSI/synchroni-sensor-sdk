import os
import re

from setuptools import setup, find_packages

this_directory = os.path.abspath(os.path.dirname(__file__))
with open(os.path.join(this_directory, "README.md"), "r", encoding="utf-8") as f:
    long_description = f.read()

with open(os.path.join(this_directory, "sensor", "__init__.py"), "r", encoding="utf-8") as f:
    version = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', f.read(), re.M).group(1)

setup(
    name="synchroni-sensor-sdk",
    version=version,
    description="Python sdk for Synchroni",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/SynchroniSI/synchroni-sensor-sdk",
    author="Martin Ye",
    author_email="yecq_82@hotmail.com",
    packages=find_packages(),
    install_requires=["numpy", "setuptools", "bleak>=3.0.2", "flatbuffers>=25.0.0"],
    package_data={"sensor": []},
    zip_safe=True,
    python_requires=">=3.10.0",
)
