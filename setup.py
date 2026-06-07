#!/usr/bin/env python
"""JIDRA: Enterprise Java Context Backend for LLM Workflows"""

from setuptools import setup, find_packages
from pathlib import Path

readme_file = Path(__file__).parent / "README.md"
long_description = readme_file.read_text(encoding="utf-8") if readme_file.exists() else ""

setup(
    name="jidra",
    version="1.0.0",
    description="87-95% LLM token reduction for Java code analysis",
    long_description=long_description,
    long_description_content_type="text/markdown",
    author="JIDRA Contributors",
    license="MIT",
    url="https://github.com/akhilsinghcodes/jidra",

    packages=find_packages(),

    entry_points={
        "console_scripts": ["jidra=jidra.cli:main"],
    },

    install_requires=[
        "pyyaml>=6.0",
        "tree-sitter>=0.20.0",
        "tree-sitter-java>=0.20.0",
        "litellm>=1.0.0",
        "anthropic>=0.7.0",
        "requests>=2.28.0",
        "dataclasses-json>=0.5.0",
        "click>=8.0.0",
        "colorama>=0.4.6",
    ],

    extras_require={
        "dev": ["pytest>=7.0.0", "black>=22.0.0", "mypy>=0.990"],
        "docker": ["docker>=5.0.0"],
    },

    python_requires=">=3.9",

    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
    ],

    keywords=[
        "java", "code-analysis", "llm", "context-reduction",
        "spring", "actuator", "token-optimization"
    ],

    project_urls={
        "Bug Tracker": "https://github.com/akhilsinghcodes/jidra/issues",
        "Documentation": "https://github.com/akhilsinghcodes/jidra#readme",
        "Source": "https://github.com/akhilsinghcodes/jidra",
    },
)
