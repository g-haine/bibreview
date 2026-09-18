# Installation

BibReview currently targets **Python 3.12 or newer** and exposes the
**bibreview** command-line program.

At this stage, installing from the Git repository is the reference installation
method.

## Prerequisites

Install:

- Git;
- Python 3.12 or newer;
- a terminal;
- optionally Ruby/Bundler if you also want to preview a Jekyll site locally.

Check Python with:

~~~bash
python --version
~~~

On some systems use **python3** or the Windows **py** launcher instead.

## Linux

~~~bash
git clone https://github.com/g-haine/bibreview.git
cd bibreview

python3 -m venv .venv
source .venv/bin/activate

python -m pip install --upgrade pip
python -m pip install -e .

bibreview --version
~~~

Run the test suite with:

~~~bash
python -m unittest discover -s tests -v
~~~

## macOS

~~~bash
git clone https://github.com/g-haine/bibreview.git
cd bibreview

python3 -m venv .venv
source .venv/bin/activate

python -m pip install --upgrade pip
python -m pip install -e .

bibreview --version
~~~

If **python3 --version** is older than 3.12, install a current Python first,
then recreate the virtual environment.

## Windows

PowerShell:

~~~powershell
git clone https://github.com/g-haine/bibreview.git
cd bibreview

py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1

python -m pip install --upgrade pip
python -m pip install -e .

bibreview --version
~~~

If PowerShell refuses to activate the virtual environment because of the local
execution policy, either follow your institution's PowerShell policy or use
Command Prompt:

~~~bat
.venv\Scripts\activate.bat
~~~

Run the tests with:

~~~powershell
python -m unittest discover -s tests -v
~~~

## Install a pinned revision without cloning BibReview

For CI or another project, install a known tag or commit:

~~~bash
python -m pip install "git+https://github.com/g-haine/bibreview.git@<TAG-OR-COMMIT>"
~~~

Pinning a tag or commit is strongly recommended for reproducible websites.
Avoid installing an unpinned **main** in production automation.

## Upgrading

For an editable local checkout:

~~~bash
git pull
python -m pip install -e .
~~~

For a project that installs a pinned revision, update the pin deliberately,
validate the project, render it, inspect the Git diff, and only then commit the
new pin.

## Create a project

BibReview projects should normally live in their own repository. A typical
project contains:

~~~text
bibreview.yml
data/
bib/
archive/
site/
~~~

Start from [bibreview.example.yml](../bibreview.example.yml), then continue with
[Configuration](configuration.md).
