import discord
import json
import random
import re

from typing import Optional, Tuple, List, Dict
from pprint import pformat

client = discord.Client()
_dice_types: Optional[dict]

simple_numeric_pattern = re.compile(
    r'^\d+$'
)

base_roll_string = re.compile(
    r'(?P<num_dice>\d+)[dD](?P<dice_type>\d+|[A-Z]+)(?P<options>.*?)'
)

supported_operators = re.compile(
    r'([+-])'
)


# -------------------------------------------------------------
#  Helpers
# -------------------------------------------------------------

class UnknownDiceTypeError(Exception):
    def __init__(self, dice_type, message=''):
        self.dice_type = dice_type
        self.message = message
        super().__init__(self.message)

    def __str__(self):
        return f'{self.dice_type} is undefined. {self.message}'


class UnknownDiceValueError(UnknownDiceTypeError):
    def __init__(self, dice_type, dice_roll, message=''):
        self.dice_roll = dice_roll
        super().__init__(dice_type, message)

    def __str__(self):
        return f'{self.dice_roll} has no value in {self.dice_type}. {self.message}'


class UnknownOperationError(Exception):
    def __init__(self, op, message=''):
        self.op = op
        self.message = message
        super().__init__(self.message)

    def __str__(self):
        return f'{self.op} is not a supported operation. {self.message}'


# -------------------------------------------------------------
#  Rolling Functions
# -------------------------------------------------------------


def decode_dice(dice_type) -> Dict:
    # Test if the dice info can be generated easily
    simple_dice_match = simple_numeric_pattern.match(dice_type)
    # If it can, do it, otherwise try to load the dice info
    if simple_dice_match:
        dice_info = {
            'sides': int(simple_dice_match.group()),
        }
        if dice_info['sides'] < 1:
            raise UnknownDiceTypeError(dice_type, "Illegal numeric dice!")
    else:
        try:
            dice_info = _dice_types[dice_type]
        except KeyError:
            raise UnknownDiceTypeError(dice_type)

    dice_map = dice_info['map'] if 'map' in dice_info else range(1, dice_info['sides'] + 1)
    dice_map = [str(entry) for entry in dice_map]
    dice_values = dice_info['value'] if 'value' in dice_info else {}

    # FIXME - Convert to DiceType class
    dice_dict = {
        'sides': dice_info['sides'],
        'map': dice_map,
        'value': dice_values,
    }

    return dice_dict


def roll_and_decode_dice(num_dice, dice_type):
    # Get the dice info
    dice_dict = decode_dice(dice_type)

    # Roll the dice

    dice_rolls = [random.randint(0, dice_dict['sides'] - 1) for x in range(num_dice)]

    # Resolve the dice
    dice_faces = [dice_dict['map'][roll] for roll in dice_rolls]

    # Calculate dice values if there's symbolic dice names
    dice_results = []
    for roll in dice_faces:
        try:
            dice_results.append(int(roll))
        except ValueError:
            try:
                dice_results.append(dice_dict['value'][roll])
            except KeyError:
                raise UnknownDiceValueError(roll, dice_type)

    dice_dict['faces'] = dice_faces
    dice_dict['results'] = dice_results
    dice_dict['rolls'] = dice_rolls

    dice_sum = sum(dice_results)
    dice_dict['sum'] = dice_sum

    return dice_dict


def roll_command(command_str: str):
    math_strings = [x.strip() for x in supported_operators.split(command_str)]
    dice_strings = math_strings[::2]
    operator_strings = math_strings[1::2]
    operator_strings.insert(0, '+')

    rolls = []

    for dice_str in dice_strings:
        if num_match := simple_numeric_pattern.match(dice_str):
            # roll is actually just a modifier number
            dice_dict = {
                'results': [int(num_match.group(0))],
            }
        elif dice_str == '':
            dice_dict = {
                'results': [0],
            }
        else:
            roll_match = base_roll_string.match(dice_str)
            num_dice = int(roll_match.group('num_dice'))
            dice_type = roll_match.group('dice_type')
            dice_options = roll_match.group('options')
            dice_dict = roll_and_decode_dice(num_dice, dice_type)
            dice_dict['options_str'] = dice_options

        dice_dict['original_string'] = dice_str
        rolls.append(dice_dict)

    # Start resolving dice rolls
    total_sum = 0
    for op, roll in zip(operator_strings, rolls):
        if op == '+':
            total_sum += sum(roll['results'])
        elif op == '-':
            total_sum -= sum(roll['results'])
        else:
            raise UnknownOperationError(op, f'Used before {roll["original_string"]}')

    result_dict = {
        'Total Sum': total_sum,
        'Rolls': rolls,
    }

    return result_dict


# -------------------------------------------------------------
#  Actual Discord Bot
# -------------------------------------------------------------

@client.event
async def on_ready():
    print('We have logged in as {0.user}'.format(client))


@client.event
async def on_message(message):
    if message.author == client.user:
        return

    if message.content.startswith('-hello'):
        await message.channel.send('Hello!')

    elif message.content.startswith('-r '):
        results = roll_command(message.content[2:])
        response = f'```{pformat(results)}```'
        await message.channel.send(response)


if __name__ == '__main__':
    with open('env.json', 'r') as env_file:
        env = json.load(env_file)

    with open('dice.json', 'r') as dice_file:
        _dice_types = json.load(dice_file)

    discord_token = env['TOKEN']
    client.run(discord_token)
