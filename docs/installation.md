# Installation

BibReview **v1.6.29** requires **Python 3.12 or newer** and exposes the
**bibreview** command-line program.

For normal use, install an exact release tag. Pinning the version keeps local
projects and automated websites reproducible.

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
python3 -m venv .venv
source .venv/bin/activate

python -m pip install --upgrade pip
python -m pip install "git+https://github.com/g-haine/bibreview.git@v1.6.29"

bibreview --version
~~~

The final command should report **bibreview 1.6.29**.

## macOS

~~~bash
python3 -m venv .venv
source .venv/bin/activate

python -m pip install --upgrade pip
python -m pip install "git+https://github.com/g-haine/bibreview.git@v1.6.29"

bibreview --version
~~~

If **python3 --version** is older than 3.12, install a current Python first,
then recreate the virtual environment.

## Windows

PowerShell:

~~~powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1

python -m pip install --upgrade pip
python -m pip install "git+https://github.com/g-haine/bibreview.git@v1.6.29"

bibreview --version
~~~

If PowerShell refuses to activate the virtual environment because of the local
execution policy, either follow your institution's PowerShell policy or use
Command Prompt:

~~~bat
.venv\Scripts\activate.bat
~~~

## Install another pinned revision

A different release tag or exact commit can be installed explicitly:

~~~bash
python -m pip install "git+https://github.com/g-haine/bibreview.git@<TAG-OR-COMMIT>"
~~~

For production automation, prefer an exact release such as **v1.6.29** or an
exact commit. Avoid installing an unpinned **main**.

## Development checkout

Contributors or users intentionally following development can install an
editable checkout:

~~~bash
git clone https://github.com/g-haine/bibreview.git
cd bibreview

python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .

python -m unittest discover -s tests -v
~~~

On Windows, use the activation command shown above.

## Upgrading

For a project using a stable release, update the release pin deliberately. Then
validate the project, render it, inspect the Git diff, and only then commit the
new pin.

For an editable development checkout:

~~~bash
git pull
python -m pip install -e .
~~~

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
