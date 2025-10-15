FROM --platform=linux/amd64 public.ecr.aws/lambda/python:3.12

# Install system dependencies for CADQuery and Trimesh
RUN dnf update -y && \
    dnf install -y \
    gcc \
    g++ \
    make \
    cmake \
    libXext-devel \
    libXrender-devel \
    libXt-devel \
    mesa-libGL-devel \
    mesa-libGLU-devel \
    freetype-devel \
    libpng-devel \
    git && \
    dnf clean all

# Copies requirements.txt file into the container
COPY requirements.txt ./

# Installs dependencies found in your requirements.txt file
RUN pip install -r requirements.txt

# Be sure to copy over the function itself!
COPY . .

# Points to the handler function of your lambda function
CMD ["lambda.lambda_handler"]
