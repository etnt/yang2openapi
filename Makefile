
.PHONY: all
all: pyvenv install-requirements

.PHONY: yang2openapi
yang2openapi:
	./pyvenv/bin/python3 ./src/yang2openapi.py


#
# $ . pyvenv/bin/activate
#
pyvenv:
	#virtualenv $@
	#	python3 -m venv --system-site-packages $@
	python3 -m venv $@
	$@/bin/pip $(PIP_OPTS) install pip --upgrade


.PHONY: install-requirements
install-requirements: pyvenv
	$</bin/pip $(PIP_OPTS) install -r ./requirements.txt
	touch $@


.PHONY: clean
clean:
	rm -rf pyvenv* __pycache__
	rm -f install-requirements
