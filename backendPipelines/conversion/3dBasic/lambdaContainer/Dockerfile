FROM --platform=linux/amd64 public.ecr.aws/lambda/python:3.12

#Note: Lambda containers automatically use a non-root user that is created in the base image

# Installs python, removes cache file to make things smaller (needed for lambda container python 3.10 but not 3.12)
# RUN yum update -y && \
#     yum install -y python3 python3-dev python3-pip gcc git && \
#     rm -Rf /var/cache/yum

# Copies requirements.txt file into the container
COPY requirements.txt ./
# Installs dependencies found in your requirements.txt file
RUN pip install -r requirements.txt

# Be sure to copy over the function itself!
COPY . .

# Points to the handler function of your lambda function
CMD ["lambda.lambda_handler"]