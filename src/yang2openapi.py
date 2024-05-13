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
from logging import FileHandler
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


# Create a logger
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Create a handler that truncates the log file every time the program is run
handler = FileHandler("./logs/yang2openapi.log", mode='w')
handler.setFormatter(logging.Formatter('%(name)s : %(levelname)-8s : %(message)s'))

# Add the handler to the logger
logger.addHandler(handler)


# Create a class to hold the configuration and other stateful data
class Config:
    def __init__(self):
        self.start_by_improving = False
        self.validate_only = False
        self.model = 'gpt-4'
        self.verbose = False
        self.default_temperature = 0.7
        self.server_url = 'http://localhost:8080/restconf/data'
        self.infile = None
        self.outfile = None
        self.infile_content = None
        self.user_interactive = False

def parse_args() -> str:
    parser = argparse.ArgumentParser(description='Ask an AI model for answer to you question')

    parser.add_argument('-i', '--infile', type=str, help='Input file')
    parser.add_argument('-o', '--outfile', type=str, help='Output file')
    parser.add_argument('-m', '--model', type=str, default='gpt-4', help='Set OpenAPI to use (default: gpt-4)')
    parser.add_argument('-t', '--time', action='store_true' , help='Output some runtime info')
    parser.add_argument('-u', '--user-interactive', action='store_true' , help='You will be prompted for improvment instructions.')
    parser.add_argument('--validate', action='store_true' , help='Validate OpenAPI <infile>')
    parser.add_argument('--improve', action='store_true' , help='Improve OpenAPI <infile>')
    parser.add_argument('--temperature', type=float, default=0.7, help='Set the temperature for creativity (default: 0.7)')
    parser.add_argument('-s', '--server-url', type=str, default='http://localhost:8080/restconf/data', help='Set the server URL in the OpenAPI')
    parser.add_argument('-v', '--verbose', action='store_true' , help='Output some debug info')

    args = parser.parse_args()

    if args.time:
        os.environ['YANG2OPENAPI_RUNTIME'] = 'True'

    config = Config()
    config.validate_only = args.validate
    config.model = args.model
    config.verbose = args.verbose
    config.default_temperature = args.temperature
    config.server_url = args.server_url
    config.infile = args.infile
    config.outfile = args.outfile
    config.user_interactive = args.user_interactive
    config.start_by_improving = args.improve

    return config


def log_execution_time(func):
    """Decorator to log the execution time of a function.
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        start_time = time.time()
        result = func(*args, **kwargs)
        end_time = time.time()
        execution_time = end_time - start_time
        if os.getenv('YANG2OPENAPI_RUNTIME'):
            print(f"{func.__name__} executed in {execution_time:.4f} seconds")
        return result

    return wrapper

class Spinner:
    """Display some information while waiting for the AI model to respond.
    """
    def __init__(self, message='Loading...'):
        #self.spinner = itertools.cycle(['-', '/', '|', '\\'])
        self.spinner = itertools.count(0, 1)
        self.thread = threading.Thread(target=self.spin)
        self.running = False
        self.message = message

    def start(self):
        self.running = True
        self.thread.start()

    def spin(self):
        while self.running:
            print(f"LLM is working: {next(self.spinner)}", end='\r')
            #time.sleep(0.1)
            time.sleep(1.0)

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


general_system_rules = """
- Each and every YANG element must be transformed to the corresponding OpenAPI JSON objects.
- The answer must only consist of OpenAPI JSON version 3.1.
- Be elaborate in your answer and provide as much detail as possible including all the necessary objects.
- All YANG elements must have operations for GET, POST, PUT, DELETE, and PATCH, except 'rpc' and 'action' that only can be operated on with POST.
- You must produce both Path, Path Item, and Operation Objects where an initial Path Item is prefixed with the Yang module name followed by a colon; example: 'foo:bar'.
- For Yang list and leaf-list elements, you must produce a Parameter Path Object for corresponding keys, unless the operation is a POST.
- For each path operation, you must produce a Request Body Object with an example, unless the operation is a GET.
- To conform to RESTCONF RFC 8040, paths with a key must look like this: '/foo:bar={key1}'.
- The content media type must be 'application/yang-data+json'.
- All GET operations have an optional query Parameter Object with the name 'depth' of type 'uint16'.
"""


system_template = """
You are an expert in Yang as specified in RFC 7950, RESTCONF as specified in RFC 8040 and OpenAPI as specified in version 3.1.
You excel in transforming Yang models to the corresponding OpenAPI JSON that conforms to the RESTCONF RFC.
You must follow the following rules:

{general_system_rules}
- Add a Security Scheme Object with 'http' and the scheme 'basic'.
- Add a Server Object with the URL: '{server_url}'.
"""

system_error_template = """
Errors was found in the OpenAPI file.
Please correct them and try again.
Reply with the complete and corrected OpenAPI JSON file.
Only JSON is accepted.
Remember to follow the following rules:

{general_system_rules}
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

Here follows the original Yang model for reference:

{yang_model}
"""

system_improve_template = """
{user_reply}
Reply with the complete and improved OpenAPI JSON file.
Only JSON is accepted.
Remember to follow the following rules:

{general_system_rules}
"""

improve_template = """
Here follows the OpenAPI JSON file, to be improved accordingly:

{openapi_json}

Here follows the original Yang model for reference:

{yang_model}
"""

@log_execution_time
def call_llm(chat_model, prompt):
    # Start the spinner
    spinner = Spinner()
    spinner.start()
    # Call the model
    try:
        result = chat_model.invoke(prompt)
    except Exception as e:
        # May end up here when the quota is exceeded.
        spinner.stop()
        print(f"<ERROR> calling model: {e}")
        exit(1)
    # Stop the spinner
    spinner.stop()
    return result



@log_execution_time
def mk_json_output(json_object, indent=4):
    try:
        json_output = json.dumps(json_object, indent=indent)
    except TypeError as e:
        print(f"<ERROR> serializing JSON: {e}")
        json_output = None
    return json_output


def read_infile(cfg: Config) -> Config:
    with open(cfg.infile, 'r') as f:
        cfg.infile_content = f.read()
    return cfg


@log_execution_time
def validate_json(file):
    spec_dict, _base_uri = read_from_filename(file)
    return OpenAPIV31SpecValidator(spec_dict).iter_errors()


def validate_json_file_and_exit(cfg: Config) -> None:
    try:
        errors_iterator = validate_json(cfg.infile)
        found_errors = False
        for error in errors_iterator:
            found_errors = True
            print(error)

        if found_errors:
            exit(1)
        else:
            print("No errors found in OpenAPI file")
            exit(0)
    except Exception as e:
        print(f"Error validating OpenAPI file: {e}")
        exit(1)



# The path is located as: ...{'paths': { <path>: <obj>, <path>: <obj>, ... }}...
def print_path_elements(json_object):
    if isinstance(json_object, dict):
        for key, value in json_object.items():
            if key == 'paths':
                for path, path_value in value.items():
                    print(f"Path: {path}")
                    if isinstance(path_value, dict):
                        print("Children keys: ", list(path_value.keys()))
            else:
                print_path_elements(value)
    elif isinstance(json_object, list):
        for item in json_object:
            print_path_elements(item)

def xprint_path_elements(json_object):
    if isinstance(json_object, dict):
        for key, value in json_object.items():
            if key == 'paths':
                for path in value.keys():
                    print(path)
            else:
                print_path_elements(value)
    elif isinstance(json_object, list):
        for item in json_object:
            print_path_elements(item)


def main(cfg: Config) -> None:

    # Validate only?
    if cfg.validate_only:
        validate_json_file_and_exit(cfg)

    # Read in the Yang file
    cfg = read_infile(cfg)

    # Create the chat model
    chat_model = ChatOpenAI(
        openai_api_key=api_key,
        model_name=cfg.model
    )
    chat_model.temperature = cfg.default_temperature

    if cfg.start_by_improving:
        # Create the initial prompt containing the OpenAPI JSON input.
        # FIXME: This is not implemented yet.
        spec_dict, _base_uri = read_from_filename(cfg.infile)
        print_path_elements(spec_dict)
        exit(0)
    else:
        # Create the initial prompt containing the YANG input.
        init_prompt = ChatPromptTemplate.from_messages([
            ("system", system_template),
            ("human" , human_template)
        ])
        prompt = init_prompt.format_messages(
            general_system_rules=general_system_rules,
            server_url=cfg.server_url,
            yang_model=cfg.infile_content
        )

    logger.info(f"INIT PROMPT: {prompt}")

    # Call the model with the initial prompt
    answer = call_llm(chat_model, prompt)

    # If the LLM do not return a JSON object we need to keep
    # track of the previous good JSON object to be able to
    # potentially have it returned to be improved upon user request.
    json_output = None
    prev_json_object = None

    # Counter to be displayed in the prompt, indicating the number of iterations.
    i = 0

    # -------------------------------------------------------------
    # Loop until the user is satisfied with the answer.
    # -------------------------------------------------------------
    while True:
        i += 1
        logger.info(f"ANSWER:\n{answer.content}")
    
        json_iter_object = extract_json_object(answer.content)
        # Get the next (and in this case, the only) item from the generator.
        json_object = next(json_iter_object, None)

        if json_object:
            prev_json_object = json_object

        # -------------------------------------------------------------
        # If we got a JSON object, write it to the output file and validate it.
        # -------------------------------------------------------------
        errors_found = False
        if cfg.outfile and json_object:
            json_output = mk_json_output(json_object)
            outfile = cfg.outfile
            with open(outfile, 'w') as f:
                f.write(json_output)
            # Validate the OpenAPI spec we got from the AI model.
            errors = []
            try:
                # Note: The OpenAPI spec validator may crash if e.g schema components are missing.
                # Hence we need to catch that and produce an error message to be returned to the LLM.
                errors_iterator = validate_json(outfile)
                for error in errors_iterator:
                    logger.info(f"ERROR: {error}")
                    errors.append(error)
                    errors_found = True
            except Exception as e:
                errors_found = True
                errors.append(str(e))

        # -------------------------------------------------------------
        # If errors were found, prompt for a new corrected answer from the LLM.
        # -------------------------------------------------------------
        if errors_found and json_object:
            error_prompt = ChatPromptTemplate.from_messages([
                ("system", system_error_template),
                ("human" , error_template)
            ])
            json_output = mk_json_output(json_object, indent=None)
            prompt = error_prompt.format_messages(
                general_system_rules=general_system_rules,
                errors=errors, 
                openapi_json=json_output,
                yang_model=cfg.infile_content
            )
            logger.info(f"ERROR PROMPT:\n{prompt}")
            print(f"<{i}> Got {len(errors)} errors in the OpenAPI JSON; prompting for new answer")
            answer = call_llm(chat_model, prompt)
            continue

        # -------------------------------------------------------------
        # Ask the user if the OpenAPI JSON should be further improved.
        # -------------------------------------------------------------
        if cfg.user_interactive:
            print("Give instructions for improving the OpenAPI JSON ('quit' to exit):")
            user_reply = input(f"{i}>: ")
            if user_reply.lower() == "quit":
                break
            else:
                improve_prompt = ChatPromptTemplate.from_messages([
                    ("system", system_improve_template),
                    ("human" , improve_template)
                ])
                if json_object:
                    json_output = mk_json_output(json_object, indent=None)
                elif prev_json_object:
                    json_output = mk_json_output(prev_json_object, indent=None)
                else:
                    print("<ERROR> No JSON object to improve! Exiting.")
                    break
                prompt = improve_prompt.format_messages(
                    general_system_rules=general_system_rules,
                    user_reply=user_reply,
                    openapi_json=json_output,
                    yang_model=cfg.infile_content
                )
                logger.info(f"IMPROVE PROMPT:\n{prompt}")
                answer = call_llm(chat_model, prompt)
                continue



if __name__ == "__main__":
    # Parse the command line arguments
    cfg = parse_args()

    # Call the main function with the parsed arguments
    main(cfg)