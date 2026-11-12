FROM public.ecr.aws/docker/library/python:3.13-slim-bookworm@sha256:01f42367a0a94ad4bc17111776fd66e3500c1d87c15bbd6055b7371d39c124fb

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONHASHSEED=0 \
    OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1

RUN python -m pip install --no-cache-dir \
    numpy==2.1.3 \
    scikit-learn==1.5.2 \
    scipy==1.18.1 \
    joblib==1.6.0 \
    threadpoolctl==3.8.0 \
    cloudpickle==3.1.2
COPY worker.py /opt/covtype-worker.py
RUN chmod 0555 /opt/covtype-worker.py
WORKDIR /tmp
USER 65532:65532
ENTRYPOINT ["python", "/opt/covtype-worker.py"]
