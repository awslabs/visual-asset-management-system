#!/bin/sh

export AWS_REGION=us-east-1
export STACK_NAME=ilya
export DOCKER_DEFAULT_PLATFORM=linux/amd64
npm run deploy.dev adminEmailAddress=kenny.cheang@opsguru.io
