# VAMS Cosmos Predict Pipeline Container
# Based on the official NVIDIA Cosmos-Predict1 Dockerfile pattern
# https://github.com/nvidia-cosmos/cosmos-predict1/blob/main/Dockerfile

FROM nvcr.io/nvidia/pytorch:24.10-py3

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# Install system dependencies
RUN apt-get update && apt-get install -y git tree ffmpeg wget curl unzip
RUN rm /bin/sh && ln -s /bin/bash /bin/sh && ln -s /lib64/libcuda.so.1 /lib64/libcuda.so

# Install AWS CLI v2
RUN curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip" && \
    unzip awscliv2.zip && \
    ./aws/install && \
    rm -rf aws awscliv2.zip

# Clone cosmos-predict1 repository
RUN git clone https://github.com/nvidia-cosmos/cosmos-predict1.git /opt/cosmos-predict1

# Copy yaml and requirements into container (needed for conda env setup)
RUN cp /opt/cosmos-predict1/cosmos-predict1.yaml /cosmos-predict1.yaml && \
    cp /opt/cosmos-predict1/requirements.txt /requirements.txt

# Install all dependencies in a single RUN matching the official NVIDIA Dockerfile exactly.
# This includes: miniconda, conda env, pip requirements, symlinks, transformer-engine, APEX
RUN echo "Installing dependencies. This will take a while..." && \
    mkdir -p ~/miniconda3 && \
    wget -q https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh -O ~/miniconda3/miniconda.sh && \
    bash ~/miniconda3/miniconda.sh -b -u -p ~/miniconda3 && \
    rm ~/miniconda3/miniconda.sh && \
    source ~/miniconda3/bin/activate && \
    conda tos accept --override-channels --channel https://repo.anaconda.com/pkgs/main && \
    conda tos accept --override-channels --channel https://repo.anaconda.com/pkgs/r && \
    conda env create --file /cosmos-predict1.yaml && \
    conda activate cosmos-predict1 && \
    pip install --no-cache-dir -r /requirements.txt && \
    ln -sf $CONDA_PREFIX/lib/python3.10/site-packages/nvidia/*/include/* $CONDA_PREFIX/include/ && \
    ln -sf $CONDA_PREFIX/lib/python3.10/site-packages/nvidia/*/include/* $CONDA_PREFIX/include/python3.10 && \
    ln -sf $CONDA_PREFIX/lib/python3.10/site-packages/triton/backends/nvidia/include/* $CONDA_PREFIX/include/ && \
    pip install transformer-engine[pytorch]==1.12.0 && \
    git clone https://github.com/NVIDIA/apex && cd apex && \
    CUDA_HOME=$CONDA_PREFIX pip install -v --disable-pip-version-check --no-cache-dir --no-build-isolation --config-settings "--build-option=--cpp_ext" --config-settings "--build-option=--cuda_ext" . && \
    cd / && rm -rf /apex && \
    pip install --no-cache-dir boto3 huggingface_hub && \
    echo "Environment setup complete"

# Create code directory and copy VAMS container code
RUN mkdir -p /opt/ml/code
COPY __main__.py model_manager.py inference.py entrypoint.sh /opt/ml/code/
RUN chmod +x /opt/ml/code/entrypoint.sh

WORKDIR /opt/ml/code
ENTRYPOINT ["/opt/ml/code/entrypoint.sh"]
