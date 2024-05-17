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
class State:
    def __init__(self):
        self.start_by_improving = False
        self.validate_only = False
        self.model = 'gpt-4'
        self.verbose = False
        self.default_temperature = 0.7
        self.server_url = 'http://localhost:8080/restconf/data'
        self.infile = None
        self.outfile = None
        self.jsonfile = None
        self.user_rules_file = None
        self.infile_content = None
        self.user_interactive = False

    def __str__(self):
        return f"State: {self.__dict__}"

def parse_args() -> str:
    parser = argparse.ArgumentParser(description='Transform YANG to OpenAPI JSON.')

    parser.add_argument('-i', '--infile', type=str, help='Input file')
    parser.add_argument('-o', '--outfile', type=str, default="swagger.json", help='Output file (default: swagger.json)')
    parser.add_argument('-r', '--user-rules-file', type=str, default="user_rules.txt", help='Use rules file (default: user_rules.txt)')
    parser.add_argument('-m', '--model', type=str, default='gpt-4o', help='Set OpenAPI to use (default: gpt-4o)')
    parser.add_argument('-t', '--time', action='store_true' , help='Output some runtime info')
    parser.add_argument('-u', '--user-interactive', action='store_true' , help='You will be prompted for improvment instructions.')
    parser.add_argument('--validate', action='store_true' , help='Validate OpenAPI <infile>')
    parser.add_argument('--improve', type=str , help='Improve OpenAPI <jsonfile>')
    parser.add_argument('--temperature', type=float, default=0.7, help='Set the temperature for creativity (default: 0.7)')
    parser.add_argument('-s', '--server-url', type=str, default='http://localhost:8080/restconf/data', help='Set the server URL in the OpenAPI')
    parser.add_argument('-v', '--verbose', action='store_true' , help='Output some debug info')

    args = parser.parse_args()

    if args.time:
        os.environ['YANG2OPENAPI_RUNTIME'] = 'True'

    state = State()
    state.validate_only = args.validate
    state.model = args.model
    state.verbose = args.verbose
    state.default_temperature = args.temperature
    state.server_url = args.server_url
    state.infile = args.infile
    state.outfile = args.outfile
    state.user_rules_file = args.user_rules_file
    state.user_interactive = args.user_interactive
    if args.improve:
        # We will use an existing OpenAPI JSON file as a starting point to improve upon.
        state.start_by_improving = True
        state.jsonfile = args.improve

    return state


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
- Each and every YANG element must be transformed to the corresponding OpenAPI JSON objects as specified in the RESTCONF RFC 8040.

- The answer must only consist of OpenAPI JSON version 3.1.

{restconf_rules}

- All YANG elements must have operations for GET, POST, PUT, DELETE, and PATCH, except 'rpc' and 'action' that only can be operated on with POST.

- You must produce both Path, Path Item, and Operation Objects where an initial Path Item is prefixed with the Yang module name followed by a colon; example: 'foo:bar'.

- For Yang list and leaf-list elements, you must produce a Parameter Path Object for corresponding keys, unless the operation is a POST.

- For each path operation, you must produce a Request Body Object with an example, unless the operation is a GET.

- Add a Security Scheme Object with 'http' and the scheme 'basic'.

- Add a Server Object with the URL: '{server_url}'.

- When creating a presence container, an empty object should be sent as the payload.

- Vhen creating or modifying a YANG data resource a return code '204 No Content' will be returned at success.

- The content media type must be 'application/yang-data+json'.

- All GET operations have an optional query Parameter Object with the name 'depth' of type 'uint16'.
"""

restconf_rules = """
- The '/restconf/data' subtree represents the datastore resource, which is a collection of configuration data and state data nodes.

- A data resource represents a YANG data node that is a descendant node of a datastore resource.
  Containers, leafs, leaf-list entries, list entries, anydata nodes, and anyxml nodes are data resources.

- The top-level data node MUST begin with a module name followed by a colon, and the module name MUST be followed by a data node name.
  Example: '/restconf/data/ietf-interfaces:interfaces'.

- The GET method is used to retrieve data for a resource. It is supported for all resource types, except operation resources.

- The POST method is used to create a new resource. It is supported for all resource types, except operation resources.
  The target resource for the POST method for resource creation is the parent of the new resource.
  The message-body is expected to contain the content of a child resource to create within the parent (targetresource).

- The PUT method is used to create or replace the target data resource.
  The target resource for the PUT method for resource creation is the new resource.

- The PATCH method can be used to create or update, but not delete, a child resource within the target resource.

- The DELETE method is used to delete the target resource.
"""


system_template = """
You are an expert in Yang as specified in RFC 7950, RESTCONF as specified in RFC 8040 and OpenAPI as specified in version 3.1.
You excel in transforming Yang models to the corresponding OpenAPI JSON that conforms to the RESTCONF RFC.
You must follow the following rules:

{general_system_rules}

{user_rules}
"""

system_error_template = """
Errors was found in the OpenAPI file.
Please correct them and try again.
Reply with the complete and corrected OpenAPI JSON file.
Only JSON is accepted.
Remember to follow the following rules:

{general_system_rules}

{user_rules}
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

{user_rules}
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


def read_infile(state: State) -> State:
    with open(state.infile, 'r') as f:
        state.infile_content = f.read()
    return state


@log_execution_time
def validate_json(file):
    spec_dict, _base_uri = read_from_filename(file)
    return OpenAPIV31SpecValidator(spec_dict).iter_errors()


def validate_json_file_and_exit(state: State) -> None:
    try:
        errors_iterator = validate_json(state.infile)
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



def main(state: State) -> None:

    # Validate only?
    if state.validate_only:
        validate_json_file_and_exit(state)

    # Read in the Yang file
    state = read_infile(state)

    # Create the chat model
    chat_model = ChatOpenAI(
        openai_api_key=api_key,
        model_name=state.model
    )
    chat_model.temperature = state.default_temperature

    # Counter to be displayed in the prompt, indicating the number of iterations.
    i = 0

    # If the LLM do not return a JSON object we need to keep
    # track of the previous good JSON object to be able to
    # potentially have it returned to be improved upon user request.
    json_output = None
    prev_json_object = None

    if state.user_rules_file:
        try:
            with open(state.user_rules_file, 'r') as f:
                user_rules = f.read()
        except FileNotFoundError:
            user_rules = ""

    if state.start_by_improving:
        print("Give instructions for improving the OpenAPI JSON ('quit' to exit):")
        user_reply = input(f"{i}>: ")
        if user_reply.lower() == "quit":
            exit(0)
        else:
            improve_prompt = ChatPromptTemplate.from_messages([
                ("system", system_improve_template),
                ("human" , improve_template)
            ])
            with open(state.jsonfile, 'r') as f:
                json_input = f.read()
            prompt = improve_prompt.format_messages(
                general_system_rules=general_system_rules,
                restconf_rules=restconf_rules,
                user_rules=user_rules,
                user_reply=user_reply,
                openapi_json=json_input,
                yang_model=state.infile_content
            )
    else:
        # Create the initial prompt containing the YANG input.
        init_prompt = ChatPromptTemplate.from_messages([
            ("system", system_template),
            ("human" , human_template)
        ])
        prompt = init_prompt.format_messages(
            general_system_rules=general_system_rules,
            restconf_rules=restconf_rules,
            user_rules=user_rules,
            server_url=state.server_url,
            yang_model=state.infile_content
        )

    logger.info(f"START PROMPT: {prompt}")

    # Call the model with the initial prompt
    answer = call_llm(chat_model, prompt)
    
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
        if state.outfile and json_object:
            json_output = mk_json_output(json_object)
            outfile = state.outfile
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
                restconf_rules=restconf_rules,
                user_rules=user_rules,
                errors=errors, 
                openapi_json=json_output,
                yang_model=state.infile_content
            )
            logger.info(f"ERROR PROMPT:\n{prompt}")
            print(f"<{i}> Got {len(errors)} errors in the OpenAPI JSON; prompting for new answer")
            answer = call_llm(chat_model, prompt)
            continue

        # -------------------------------------------------------------
        # Ask the user if the OpenAPI JSON should be further improved.
        # -------------------------------------------------------------
        if state.user_interactive:
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
                    restconf_rules=restconf_rules,
                    user_rules=user_rules,
                    user_reply=user_reply,
                    openapi_json=json_output,
                    yang_model=state.infile_content
                )
                if state.user_rules_file:
                    with open(state.user_rules_file, 'a') as f:
                        f.write('\n' + user_reply + '\n')
                logger.info(f"IMPROVE PROMPT:\n{prompt}")
                answer = call_llm(chat_model, prompt)
                continue



if __name__ == "__main__":
    # Parse the command line arguments
    state = parse_args()

    if state.verbose:
        print(state)

    # Call the main function with the parsed arguments
    main(state)
