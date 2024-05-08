# yang2openapi - Yang to OpenAPI generator using AI
> Just some AI experimenting

Experiment trying to make use of GPT to create an OpenAPI 3.1
specification from a Yang file.
    
When an answer, containing JSON, is received it is validated
to make sure it conform to the OpenAPI 3.1 specification,
and if not, the errors will be returned to the GPT asking it
to correct the errors and return an updated spec. 

This is repeated until no more errors are found.

## Run

Create a virtual env and install the dependencies:

```shell
make
. ./pyvenv/bin/activate
```

Put your OPENAI API key in a `.env` file, like:

```shell
OPENAI_API_KEY="xxxxxxxx"
```

Then run the script:

```shell
python3 ./src/yang2openapi.py  -t --verbose --infile ./data/example.yang --outfile swagger.json
```

To try the output you could run the `swagger-ui` in a docker container. In the example below,
remember to copy the `swagger.json` to the /tmp/shared_docker_dir.

```shell
# Start a docker container running the Swagger UI
$ docker run -d -p 7080:8080 -e SWAGGER_JSON=/foo/swagger.json -v /tmp/shared_docker_dir:/foo swaggerapi/swagger-ui

# Then point your browser to:
http://localhost:7080/

```