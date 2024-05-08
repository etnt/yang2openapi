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

## Demo

Here is a [demo](https://youtu.be/rcrAmRjqsPE?si=MPIMNwcx4a7ieIB7) that show how we build
an OpenAPI JSON code from a simple Yang model. This is done interactively with auto error correction.


## Help

```shell
$ python3 src/yang2openapi.py  --help
usage: yang2openapi.py [-h] [-i INFILE] [-o OUTFILE] [-m MODEL] [-t] [-u] [--validate] [--temperature TEMPERATURE] [-s SERVER_URL] [-v]

Ask an AI model for answer to you question

options:
  -h, --help            show this help message and exit
  -i INFILE, --infile INFILE
                        Input Yang file
  -o OUTFILE, --outfile OUTFILE
                        Output OpenAPI file
  -m MODEL, --model MODEL
                        Set OpenAPI to use (default: gpt-4)
  -t, --time            Output some runtime info
  -u, --user-interactive
                        You will be prompted for improvment instructions.
  --validate            Validate OpenAPI <infile>
  --temperature TEMPERATURE
                        Set the temperature for creativity (default: 0.2)
  -s SERVER_URL, --server-url SERVER_URL
                        Set the server URL in the OpenAPI
  -v, --verbose         Output some debug info
```

