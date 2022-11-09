This package uses ![poetry](https://python-poetry.org/) for dependency management.

Make sure you have poetry installed and your local python version is using the python 3.9.

### How to build this package
The Dockerfile should do everything you would need. If you included new dependencies make sure you export them into the requirements file.
Sometimes creating the container image fails in presence of the poetry.lock file so please remove the poetry.lock file once your dependecies have been exported.

More Details:
https://github.com/aws/aws-cdk/issues/14201

### Adding New Dependencies
`poetry add stepfunctions`

### Remove poetry.lock file
https://github.com/aws/aws-cdk/issues/14201

### Export poetry dependecies to requirements.txt
`poetry export -o requirements.txt --without-hashes`

