Build: 
docker build --rm -f Dockerfile -t potreepipeline:latest --build-arg VERSION=0.0.0 .


Windows Local Run (must turn on debugging lines in docker python build):
docker run -it -v C:/Projects/Entwine/input:/data/input:rw potreepipeline:latest
