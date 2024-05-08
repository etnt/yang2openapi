from langchain_openai import ChatOpenAI
from langchain.prompts.chat import ChatPromptTemplate
from langchain.schema import BaseOutputParser
import threading
import time
import functools
import json
from dotenv import load_dotenv
import os
import argparse
import itertools
from openapi_spec_validator import validate
from openapi_spec_validator.readers import read_from_filename
from openapi_spec_validator import OpenAPIV31SpecValidator
import logging
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Dict,
    Iterable,
    List,
    Optional,
    Tuple,
    Type,
)

load_dotenv()

api_key = os.getenv("OPENAI_API_KEY")


logging.basicConfig(filename="./logs/yang2openapi.log", level=logging.INFO, format='%(name)s : %(levelname)-8s : %(message)s')
logger = logging.getLogger(__name__)


def parse_args() -> str:
    parser = argparse.ArgumentParser(description='Ask an AI model for answer to you question')

    parser.add_argument('-i', '--infile', type=str, help='Input Yang file')
    parser.add_argument('-o', '--outfile', type=str, help='Output OpenAPI file')
    parser.add_argument('-m', '--model', type=str, default='gpt-4', help='Set OpenAPI to use (default: gpt-4)')
    parser.add_argument('-t', '--time', action='store_true' , help='Output some runtime info')
    parser.add_argument('-u', '--user-interactive', action='store_true' , help='You will be prompted for improvment instructions.')
    parser.add_argument('--validate', action='store_true' , help='Validate OpenAPI <infile>')
    parser.add_argument('--temperature', type=float, default=0.2, help='Set the temperature for creativity (default: 0.2)')
    parser.add_argument('-s', '--server-url', type=str, default='http://localhost:8080/restconf/data', help='Set the server URL in the OpenAPI')
    parser.add_argument('-v', '--verbose', action='store_true' , help='Output some debug info')


    args = parser.parse_args()

    if not args.infile:
        print(f"Error: No input file specified. Use --help to see valid input.")
        exit(1)

    if args.verbose:
        os.environ['QSEARCH_VERBOSE'] = 'True'
    
    if args.time:
        os.environ['QSEARCH_RUNTIME'] = 'True'

    return args


def log_execution_time(func):
    """Decorator to log the execution time of a function."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        start_time = time.time()
        result = func(*args, **kwargs)
        end_time = time.time()
        execution_time = end_time - start_time
        if os.getenv('QSEARCH_RUNTIME'):
            print(f"{func.__name__} executed in {execution_time:.4f} seconds")
        return result

    return wrapper

class Spinner:
    def __init__(self, message='Loading...'):
        self.spinner = itertools.cycle(['-', '/', '|', '\\'])
        self.thread = threading.Thread(target=self.spin)
        self.running = False
        self.message = message

    def start(self):
        self.running = True
        self.thread.start()

    def spin(self):
        while self.running:
            print(next(self.spinner), end='\r')
            time.sleep(0.1)

    def stop(self):
        self.running = False
        self.thread.join()

def print_verbose(*args) -> None:
    """
    Prints the given arguments if the QAI_VERBOSE environment variable is set.

    Args:
        *args: Variable number of arguments to be printed.

    Returns:
        None
    """
    if os.getenv('QSEARCH_VERBOSE'):
        print(*args)


def extract_json_object(text, decoder=json.JSONDecoder()) -> Iterable[Dict[str, Any]]:
    """Find the JSON object in text, and yield the decoded JSON data

    Args:
        text (str): The text to search for JSON object.
        decoder (json.JSONDecoder, optional): The JSON decoder to use. Defaults to json.JSONDecoder().

    Yields:
        dict: The decoded JSON data.

    Raises:
        None

    Returns:
        None
    """
    pos = 0
    while True:
        match = text.find('{', pos)
        if match == -1:
            break
        try:
            result, index = decoder.raw_decode(text[match:])
            yield result
            pos = match + index
        except ValueError as e:
            print_verbose(f"<error> extract_json_objects: {e}")
            pos = match + 1

# - The Yang elements: 'container', 'list', 'leaf-list' and 'leaf' can be operated on with GET, POST, PUT, DELETE, and PATCH.

system_template = """
You are an expert in Yang as specified in RFC 7950, RESTCONF as specified in RFC 8040 and OpenAPI as specified in version 3.1.
You excel in transforming Yang models to the corresponding OpenAPI JSON that conforms to the RESTCONF RFC.
You must follow the following rules:

1. The answer should consist of JSON only!
2. The OpenAPI JSON schema should be in version 3.1.
3. Be elaborate in your answer and provide as much detail as possible including all the necessary objects.
4. Every element in the YANG model should be transformed to the corresponding OpenAPI JSON objects.
5. The Yang elements: 'rpc' and 'action' can only be operated on with POST.
6. You should produce both Path, Path Item, and Operation Objects where an initial Path Item is prefixed with the Yang module name followed by a colon; example: 'foo:bar'.
7. For Yang list and leaf-list elements, you should produce a Parameter Path Object for corresponding keys, unless the operation is a POST.
8. For each path operation, you should produce a Request Body Object with an example, unless the operation is a GET.
9. The content media type should be 'application/yang-data+json'.
10. Add a Security Scheme Object with 'http' and the scheme 'basic'.
11. All GET operations have an optional query Parameter Object with the name 'depth' of type 'uint16'.
12. Add a Server Object with the URL: '{server_url}'.
"""

system_error_template = """
Errors was found in the OpenAPI file.
Please correct them and try again.
Reply with the complete and corrected OpenAPI JSON file.
Only JSON is accepted.
"""


human_template = """
Transform the following Yang model to OpenAPI JSON:

{yang_model}
"""

error_template = """
The following errors was found in the OpenAPI file:

{errors}

Here follows the incorrect OpenAPI JSON file, to be corrected accordingly:
{openapi_json}
"""

system_improve_template = """
Some improvement of the OpenAPI JSON file is needed.
Reply with the complete and improved OpenAPI JSON file.
Only JSON is accepted.
"""

improve_template = """
You need to improve the OpenAPI JSON file as described below:

{user_reply}

Here follows the OpenAPI JSON file, to be improved accordingly:
{openapi_json}
"""

@log_execution_time
def call_llm(chat_model, prompt):
    # Start the spinner
    spinner = Spinner()
    spinner.start()
    # Call the model
    result = chat_model.invoke(prompt)
    # Stop the spinner
    spinner.stop()
    return result


@log_execution_time
def validate_json(outfile):
    spec_dict, _base_uri = read_from_filename(outfile)
    return OpenAPIV31SpecValidator(spec_dict).iter_errors()


@log_execution_time
def mk_json_output(json_object):
    try:
        json_output = json.dumps(json_object, indent=4)
    except TypeError as e:
        print(f"Error serializing JSON: {e}")
        json_output = None
    return json_output


def main(args: argparse.Namespace):

    # Validate only?
    if args.validate:
        errors_iterator = validate_json(args.infile)
        for error in errors_iterator:
            print(error)
        exit(0)

    # Read in the Yang file
    with open(args.infile, 'r') as f:
        content = f.read()

    chat_model = ChatOpenAI(openai_api_key=api_key, model_name=args.model)
    # Modify the temperature to become more deterministic (default: 0.7)
    # chat_model.temperature = 0

    init_prompt = ChatPromptTemplate.from_messages([
        ("system", system_template),
        ("human" , human_template)
    ])

    prompt = init_prompt.format_messages(
        server_url=args.server_url,
        yang_model=content
    )
    answer = call_llm(chat_model, prompt)

    # Max 5 iterations!
    for i in range(5):
        logger.info(f"ANSWER: {answer.content}")
    
        json_object = extract_json_object(answer.content)
        # Get the next (and in this case, the only) item from the generator.
        json_output = mk_json_output(next(json_object))

        if args.outfile:
            outfile = args.outfile
                    
            if json_output:
                with open(outfile, 'w') as f:
                    f.write(json_output)
            else:
                print("<ERROR> No JSON object found in answer")
                exit(1)

            # Validate the OpenAPI spec we got from the AI model.
            errors_iterator = validate_json(outfile)
            errors_found = False
            errors = []
            for error in errors_iterator:
                logger.info(f"ERROR: {error}")
                errors.append(error)
                errors_found = True
        else:
            # Atm we don't know how to validate without going via file,
            # so we just print the JSON and exit.
            print(json.dumps(json_object, indent=4))
            break

        # If errors were found, prompt for a new corrected answer,
        # else break the loop to terminate.
        if errors_found:
            error_prompt = ChatPromptTemplate.from_messages([
                ("system", system_error_template),
                ("human" , error_template)
            ])
            prompt = error_prompt.format_messages(errors=errors, openapi_json=json_output)
            print_verbose(f"<{i}> Got errors in OpenAPI file, prompting for new answer")
            answer = call_llm(chat_model, prompt)
            continue

        if args.user_interactive:
            # Ask the user if the answer is correct
            print("Give instructions for improving the OpenAPI JSON ('quit' to exit):")
            user_reply = input(f"{i}>: ")
            if user_reply.lower() == "quit":
                break
            else:
                improve_prompt = ChatPromptTemplate.from_messages([
                    ("system", system_improve_template),
                    ("human" , improve_template)
                ])
                prompt = improve_prompt.format_messages(user_reply=user_reply, openapi_json=json_output)
                answer = call_llm(chat_model, prompt)
                continue



if __name__ == "__main__":
    # Parse the command line arguments
    args = parse_args()

    # Call the main function with the parsed arguments
    main(args)