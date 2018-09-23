#!/usr/bin/env python
# sudo apt-get install python-serial
#
# This file originates from Vascofazza's Retropie open OSD project.
# Author: Federico Scozzafava
#
# THIS HEADER MUST REMAIN WITH THIS FILE AT ALL TIMES
#
# This firmware is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This firmware is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this repo. If not, see <http://www.gnu.org/licenses/>.
#

import time
import os, signal, sys
from subprocess import Popen, PIPE, check_output, check_call
import re
import logging
import logging.handlers
import thread
import threading
import signal

try:
    import Adafruit_ADS1x15
except ImportError:
    exit("This library requires the Adafruit ADS1x15 module\nInstall with: sudo pip install adafruit-ads1x15")

try:
    import uinput
except ImportError:
    exit("This library requires the evdev module\nInstall with: sudo pip install uinput")

try:
    import RPi.GPIO as gpio
except ImportError:
    exit("This library requires the RPi.GPIO module\nInstall with: sudo pip install RPi.GPIO")

# Button Variables
# functionBtn = Button(22)
# volumeUpBtn = Button(12)
# volumeDownBtn = Button(6)

# Config variables
bin_dir = '/home/pi/Retropie-open-OSD/'
osd_path = bin_dir + 'osd/osd'
rfkill_path = bin_dir + 'rfkill/rfkill'

# Hardware variables
pi_charging = 26
pi_charged = 25
pi_shdn = 27
serport = '/dev/ttyACM0'

# Batt variables
voltscale = 118.0  # ADJUST THIS
currscale = 640.0
resdivmul = 4.0
resdivval = 1000.0
dacres = 33.0
dacmax = 1023.0

batt_threshold = 4
batt_full = 420
batt_low = 340
batt_shdn = 320

temperature_max = 70.0
temperature_threshold = 5.0

# Wifi variables
wifi_state = 'UNKNOWN'
wif = 0
wifi_off = 0
wifi_warning = 1
wifi_error = 2
wifi_1bar = 3
wifi_2bar = 4
wifi_3bar = 5

# Joystick Hardware settings
DZONE = 300  # dead zone applied to joystick (mV)
VREF = 1600  # joystick Vcc (mV)

# Configure Buttons
LEFT = 26
RIGHT = 13
DOWN = 6
UP = 12
BUTTON_A = 5
BUTTON_B = 7
BUTTON_X = 4
BUTTON_Y = 17
SELECT = 22
START = 15
L1 = 16
R1 = 20
HOTKEY = 22

BUTTONS = [LEFT, RIGHT, DOWN, UP, BUTTON_A, BUTTON_B,
           BUTTON_X, BUTTON_Y, SELECT, START, L1, R1]

BOUNCE_TIME = 0.01  # Debounce time in seconds

# GPIO Init
gpio.setwarnings(False)
gpio.setmode(gpio.BCM)
gpio.setup(BUTTONS, gpio.IN, pull_up_down=gpio.PUD_UP)

KEYS = {  # EDIT KEYCODES IN THIS TABLE TO YOUR PREFERENCES:
    # See /usr/include/linux/input.h for keycode names
    BUTTON_A: uinput.BTN_BASE,  # 'A' button
    BUTTON_B: uinput.BTN_BASE2,  # 'B' button
    BUTTON_X: uinput.BTN_BASE3,  # 'X' button
    BUTTON_Y: uinput.BTN_BASE4,  # 'Y' button
    SELECT: uinput.BTN_SELECT,  # 'Select' button
    START: uinput.BTN_START,  # 'Start' button
    UP: uinput.BTN_NORTH,  # Analog up
    DOWN: uinput.BTN_SOUTH,  # Analog down
    LEFT: uinput.BTN_EAST,  # Analog left
    RIGHT: uinput.BTN_WEST,  # Analog right
    10001: uinput.ABS_X + (0, VREF, 0, 0),
    10002: uinput.ABS_Y + (0, VREF, 0, 0),
}

# Global Variables

global brightness
global volt
global info
global wifi
global volume
global charge
global bat

brightness = -1
info = False
volt = -1
volume = 1
wifi = 2
charge = 0
bat = 100

# logging.basicConfig(filename='osd.log', level=logging.INFO)

# TO DO REPLACE A LOT OF OLD CALLS WITH THE CHECK_OUTPUT

adc = Adafruit_ADS1x15.ADS1015()

# Create virtual HID for Joystick
device = uinput.Device(KEYS.values())

time.sleep(1)


def handle_button(pin):
    key = KEYS[pin]
    time.sleep(BOUNCE_TIME)
    state = 0 if gpio.input(pin) else 1
    device.emit(key, state)

    device.syn()
    logging.debug("Pin: {}, KeyCode: {}, Event: {}".format(pin, key, 'press' if state else 'release'))


# Initialise Buttons
for button in BUTTONS:
    gpio.add_event_detect(button, gpio.BOTH, callback=handle_button, bouncetime=1)
    logging.debug("Button: {}".format(button))

# Send centering commands
device.emit(uinput.ABS_X, VREF / 2, syn=False);
device.emit(uinput.ABS_Y, VREF / 2);

# Set up OSD service
try:
    osd_proc = Popen(osd_path, shell=False, stdin=PIPE, stdout=None, stderr=None)
    osd_in = osd_proc.stdin
    time.sleep(1)
    osd_poll = osd_proc.poll()
    if (osd_poll):
        logging.error("ERROR: Failed to start OSD, got return code [" + str(osd_poll) + "]\n")
        sys.exit(1)
except Exception as e:
    logging.exception("ERROR: Failed start OSD binary");
    sys.exit(1);


# Check for shutdown state
def checkShdn():
    state = gpio.input(pi_shdn)
    if (state):
        logging.info("SHUTDOWN")
        doShutdown()


# Read voltage
def readVoltage():
    voltVal = adc.read_adc(0, gain=1);
    volt = int((float(voltVal) * (4.09 / 2047.0)) * 100)
    return volt


# Get voltage percent
def getVoltagepercent(volt):
    return clamp(int(float(volt - batt_shdn) / float(batt_full - batt_shdn) * 100), 0, 100)


def readVolumeLevel():
    process = os.popen("amixer | grep 'Left:' | awk -F'[][]' '{ print $2 }'")
    res = process.readline()
    process.close()

    vol = 0;
    try:
        vol = int(res.replace("%", "").replace("'C\n", ""))
    except Exception, e:
        logging.info("Audio Err    : " + str(e))

    return vol;


# Read wifi (Credits: kite's SAIO project) Modified to only read, not set wifi.
def readModeWifi(toggle=False):
    global wif
    ret = wif
    # check signal
    raw = check_output(['cat', '/proc/net/wireless'])
    strengthObj = re.search(r'.wlan0: \d*\s*(\d*)\.\s*[-]?(\d*)\.', raw, re.I)
    if strengthObj:
        strength = 0
        if (int(strengthObj.group(1)) > 0):
            strength = int(strengthObj.group(1))
        elif (int(strengthObj.group(2)) > 0):
            strength = int(strengthObj.group(2))
        logging.info("Wifi    [" + str(strength) + "]strength")
        if (strength > 55):
            ret = wifi_3bar
        elif (strength > 40):
            ret = wifi_2bar
        elif (strength > 5):
            ret = wifi_1bar
        else:
            ret = wifi_warning
    else:
        logging.info("Wifi    [---]strength")
        ret = wifi_error
    return ret


# Do a shutdown
def doShutdown(channel=None):
    check_call("sudo killall emulationstation", shell=True)
    time.sleep(1)
    check_call("sudo shutdown -h now", shell=True)
    try:
        sys.stdout.close()
    except:
        pass
    try:
        sys.stderr.close()
    except:
        pass
    sys.exit(0)


# Signals the OSD binary
def updateOSD(volt=0, bat=0, temp=0, wifi=0, audio=0, brightness=0, info=False, charge=False):
    commands = "v" + str(volt) + " b" + str(bat) + " t" + str(temp) + " w" + str(wifi) + " a" + str(audio) + " l" + str(
        brightness) + " " + ("on " if info else "off ") + ("charge" if charge else "ncharge") + "\n"
    # print commands
    osd_proc.send_signal(signal.SIGUSR1)
    osd_in.write(commands)
    osd_in.flush()


# Misc functions
def clamp(n, minn, maxn):
    return max(min(maxn, n), minn)


condition = threading.Condition()


def volumeUp():
    global volume
    volume = min(100, volume + 10)
    os.system("amixer sset -q 'PCM' " + str(volume) + "%")


def volumeDown():
    global volume
    volume = max(0, volume - 10)
    os.system("amixer sset -q 'PCM' " + str(volume) + "%")


def inputReading():
    # time.sleep(1)
    global volume
    global wifi
    while (1):
        condition.acquire()
        volt = readVoltage()
        bat = getVoltagepercent(volt)
        updateOSD(volt, bat, 20, wifi, volume, 1, info, charge)
        condition.wait(10)
        condition.release()


def checkKeyInput():
    global info

    # TODO Convert to state
    while not gpio.input(HOTKEY):
        info = True
        condition.acquire()
        condition.notify()
        condition.release()
        if not gpio.input(UP):
            volumeUp()
        elif not gpio.input(DOWN):
            volumeDown()
    info = False


def checkJoystickInput():
    an1 = adc.read_adc(2, gain=2 / 3);
    an0 = adc.read_adc(1, gain=2 / 3);

    logging.debug("X: {} | Y: {}".format(an0, an1))
    logging.debug("Above: {} | Below: {}".format((VREF / 2 + DZONE), (VREF / 2 - DZONE)))

    # Check and apply joystick states
    if (an0 > (VREF / 2 + DZONE)) or (an0 < (VREF / 2 - DZONE)):
        val = an0 - 100 - 200 * (an0 < VREF / 2 - DZONE) + 200 * (an0 > VREF / 2 + DZONE)
        device.emit(uinput.ABS_X, val)
    else:
        # Center the sticks if within deadzone
        device.emit(uinput.ABS_X, VREF / 2)
    if (an1 > (VREF / 2 + DZONE)) or (an1 < (VREF / 2 - DZONE)):
        valy = an1 + 100 - 200 * (an1 < VREF / 2 - DZONE) + 200 * (an1 > VREF / 2 + DZONE)
        device.emit(uinput.ABS_Y, valy)
    else:
        # Center the sticks if within deadzone
        device.emit(uinput.ABS_Y, VREF / 2)


def exit_gracefully(signum=None, frame=None):
    gpio.cleanup
    osd_proc.terminate()
    sys.exit(0)


signal.signal(signal.SIGINT, exit_gracefully)
signal.signal(signal.SIGTERM, exit_gracefully)

# Read Initial States
volume = readVolumeLevel()
print volume

wifi = readModeWifi();

inputReadingThread = thread.start_new_thread(inputReading, ())

# Main loop
try:
    print "STARTED!"
    while 1:
        checkKeyInput()
        checkJoystickInput()
        time.sleep(.05)
        # time.sleep(0.5)

except KeyboardInterrupt:
    exit_gracefully()
