.PHONY: test demo
test:
	python -m pytest people_reading/tests
demo:
	python -m people_reading.demo
