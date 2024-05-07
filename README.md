# yang2openapi - Yang to OpenAPI generator using AI
> Just some AI experimenting

Experiment trying to make use of GPT to create an OpenAPI 3.1
specification from a Yang file.
    
When an answer, containing JSON, is received it is validated
to make sure it conform to the OpenAPI 3.1 specification,
and if not, the errors will be returned to the GPT asking it
to correct the errors and return an updated spec. 

This is repeated until no more errors are found.
