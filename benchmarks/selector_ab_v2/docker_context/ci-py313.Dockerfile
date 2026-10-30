FROM python:3.13-bookworm
RUN python -m pip install --no-cache-dir pytest==9.2.0 pydantic==2.14.2
ENV HOME=/tmp/bananloop-home PYTHONPATH=/work/src
WORKDIR /work
