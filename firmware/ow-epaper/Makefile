SKETCH  := $(abspath .)
REPO    := $(abspath ../..)
PORT    ?= /dev/ttyACM0
# Waveshare ESP32-S3-ePaper-1.54 **V2** (ESP32-S3-PICO-1-N8R8) recommended settings.
FQBN    := esp32:esp32:esp32s3:CDCOnBoot=cdc,FlashMode=qio,FlashSize=4M,PartitionScheme=huge_app,PSRAM=opi
BUILD   := /tmp/opencode/ow-epaper-build
PYTHON  ?= $(shell [ -x $(REPO)/.venv/bin/python ] && echo $(REPO)/.venv/bin/python || echo python3)

.PHONY: test secrets build flash monitor deps

test:
	mkdir -p $(BUILD)
	g++ -std=c++17 -Wall -Wextra -Werror -o $(BUILD)/test_protocol test/test_protocol.cpp
	$(BUILD)/test_protocol
	g++ -std=c++17 -Wall -Wextra -Werror -o $(BUILD)/test_ride_log test/test_ride_log.cpp
	$(BUILD)/test_ride_log
	g++ -std=c++17 -Wall -Wextra -Werror -o $(BUILD)/test_ui test/test_ui.cpp
	$(BUILD)/test_ui

secrets.h: $(REPO)/.env tools/gen_secrets.py
	$(PYTHON) tools/gen_secrets.py > $@.tmp && mv $@.tmp $@

secrets: secrets.h

deps:
	arduino-cli lib install GxEPD2@1.6.9 NimBLE-Arduino@2.5.1

build: secrets.h test
	arduino-cli compile --fqbn $(FQBN) --build-path $(BUILD)/sketch $(SKETCH)

flash: build
	arduino-cli upload --fqbn $(FQBN) --input-dir $(BUILD)/sketch -p $(PORT) $(SKETCH)

monitor:
	arduino-cli monitor -p $(PORT) -c baudrate=115200
