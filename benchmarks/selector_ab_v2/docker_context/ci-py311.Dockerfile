FROM python:3.11-bookworm@sha256:84fa927127b46670e768371d1ad1be271d57ba93a6fbb3479045c7c6c452432f
RUN python -m pip install --no-cache-dir pytest==9.2.0 pydantic==2.14.2
ENV HOME=/tmp/bananloop-home PYTHONPATH=/work/src
WORKDIR /work
