import discord
import json
import random
import re

from typing import Optional, Tuple, List, Dict, Union
from pprint import pformat, pprint
from collections import defaultdict, Counter

_debug = True

client = discord.Client()
_dice_types: Optional[dict]

simple_numeric_pattern = re.compile(
    r'^\d+$'
)

base_roll_string = re.compile(
    r'(?P<num_dice>\d+)[dD](?P<dice_type>\d+|[A-Z]+)(?P<options>.*)'
)

supported_operators = re.compile(
    r'([+-])'
)

option_pattern = re.compile(
    r'(?P<op>[a-z]+|!|<(?!=)|>(?!=)|<=|>=)'
)

operand_pattern = re.compile(
    r'''^(\d+|['"]\w+['"])?'''
)

# -------------------------------------------------------------
#  Dice Roll Class
# -------------------------------------------------------------


class DiceRoll:
    def __init__(self, dice_str):
        # Store Dice String
        self.dice_str = dice_str

        # Prep independent attributes we'll need
        self.num_dice = 1
        self.dice_type = '1'
        self.roll_options_str = ''

        # Setup dependent attributes
        self.sides = 0
        self.map = {}
        self.map_values = {}

        # Do the initial property decode num_dice, dice_type, and roll_options
        # It also applies the map and and values
        self._decode_dice_string()

        # Now roll
        self.rolls = roll_dice(self.sides, self.num_dice)

        if _debug:
            print(self.rolls)
            print(self.faces)
            print(self.values)
            print(self.sum)

        # Do Options
        self._parse_options()

    @property
    def faces(self):
        return [self.map[roll] for roll in self.rolls]

    @property
    def values(self):
        values = []
        for roll in self.rolls:
            try:
                values.append(self.map_values[self.map[roll]])
            except KeyError:
                try:
                    values.append(int(self.map[roll]))
                except ValueError:
                    raise UnknownDiceValueError(self.dice_str, roll)
        return values

    @property
    def sum(self):
        return sum(self.values)

    def _decode_dice_string(self):
        if self.dice_str == '':
            # If doing nothing, result will be zero anyways
            dice_info = {
                'map': [0],
            }
        elif num_match := simple_numeric_pattern.match(self.dice_str):
            dice_info = {
                'map': [int(num_match.group())],
            }
        else:
            roll_match = base_roll_string.match(self.dice_str)
            self.num_dice = int(roll_match.group('num_dice'))
            self.dice_type = roll_match.group('dice_type')
            self.roll_options_str = roll_match.group('options')

            if _debug:
                print(f'Roll Options: {self.roll_options_str}')

            simple_dice_match = simple_numeric_pattern.match(self.dice_type)
            # If it can, do it, otherwise try to load the dice info
            if simple_dice_match:
                dice_info = {
                    'sides': int(simple_dice_match.group()),
                }
                if dice_info['sides'] < 1:
                    raise UnknownDiceTypeError(self.dice_type, "Illegal numeric dice!")
            else:
                try:
                    dice_info = _dice_types[self.dice_type]
                except KeyError:
                    raise UnknownDiceTypeError(self.dice_type)

        dice_map = dice_info['map'] if 'map' in dice_info else range(1, (dice_info['sides'] + 1))
        self.map = [str(entry) for entry in dice_map]
        self.sides = dice_info['sides'] if 'sides' in dice_info else len(self.map)
        self.map_values = dice_info['value'] if 'value' in dice_info else {}

    def _parse_options(self):
        option_dict: Dict[str, Union[str, int, set]]
        option_dict = defaultdict(set)

        option_string = self.roll_options_str[:]

        keep_option_found = False

        if _debug:
            print(f'Parsing {option_string}')

        while option_string:
            # Note: _ had better be empty....
            _, op, option_string = option_pattern.split(option_string, 1)

            if _debug:
                print(f'Option {op}')
            # ------------------------------
            #  First Parse Options
            # ------------------------------
            # Parsing first because the order of options may be important. Like for example, if I say 2d20!r1
            # I'm saying roll 2d20, explode (on 20), re-roll 1's. But do the re-rolls explode? Do the explodes re-roll?
            #
            # Note: This would be best replaced with a match-pattern when 3.10 becomes available
            if op == '!':
                operand, option_string = get_operand(option_string)
                # Explode on max roll only by default
                explode_on = operand if operand else str(self.sides)
                option_dict['explode'].add(explode_on)
            elif op == 'r':
                operand, option_string = get_operand(option_string)
                if operand is None:
                    raise MissingOperandError('Reroll', f'Used in {self.dice_str}')
                option_dict['reroll'].add(operand)
            elif op == 'k' and not keep_option_found:
                operand, option_string = get_operand(option_string)
                if operand is None:
                    raise MissingOperandError('Keep', f'Used in {self.dice_str}')
                option_dict['keep'] = - int(operand)
                keep_option_found = True
            elif op == 'kl' and not keep_option_found:
                operand, option_string = get_operand(option_string)
                if operand is None:
                    raise MissingOperandError('KeepLowest', f'Used in {self.dice_str}')
                option_dict['keep'] = int(operand)
                keep_option_found = True

        if _debug:
            pprint(option_dict)

        self._resolve_options(option_dict)

    def _resolve_options(self, option_dict: dict):
        # Reroll any initial dice
        for idx, face in enumerate(self.faces):
            if face in option_dict['reroll']:
                self.reroll(idx)
                if _debug:
                    print(f'Rerolled #{idx}: {self.rolls[idx]}')

        # Iteratively explode and reroll as necessary
        _iter = 0
        face_list = list(self.faces)
        while face_list:
            _iter += 1
            assert _iter < 100, "ERROR: Iteration limit reached!"
            c = Counter(face_list)
            new_rolls = []
            # Explode dice
            for exp_dice in option_dict['explode']:
                new_rolls.extend(roll_dice(self.sides, c[exp_dice]))

            new_face_list = [self.map[roll] for roll in new_rolls]

            if _debug:
                print(f'New Rolls: {new_rolls}')
                print(f'New Faces: {new_face_list}')

            # Reroll them if needed
            for idx, face in enumerate(new_face_list):
                if face in option_dict['reroll']:
                    reroll_dice(new_rolls, idx, self.sides)
                    if _debug:
                        print(f'Rerolled #{idx}: {new_rolls[idx]}')

            # Append the new rolls
            self.rolls.extend(new_rolls)
            face_list = [self.map[roll] for roll in new_rolls]

        # Now do "final" operations like keep
        if 'keep' in option_dict:
            keep_num = option_dict['keep']
            self.rolls.sort()
            if keep_num > 0:
                self.rolls = self.rolls[:keep_num]
            elif keep_num < 0:
                self.rolls = self.rolls[keep_num:]
            else:
                self.rolls = []

    def reroll(self, idx):
        self.rolls[idx] = random.randint(0, self.sides - 1)

    def get_print_dict(self):
        face_list = list(self.faces)
        print_dict = {
            'Dice String': self.dice_str,
            'Faces': face_list,
            'Rolls': list(self.rolls),
            'Counts': dict(Counter(face_list)),
            'Sum': self.sum,
        }
        return print_dict

    def __repr__(self):
        print_dict = self.get_print_dict()

        return pformat(print_dict, width=80, indent=2)


# -------------------------------------------------------------
#  Exceptions
# -------------------------------------------------------------

class UnknownDiceTypeError(Exception):
    def __init__(self, dice_type: str, message: str = ''):
        self.dice_type = dice_type
        self.message = message
        super().__init__(self.message)

    def __str__(self):
        return f'{self.dice_type} is undefined. {self.message}'


class UnknownDiceValueError(UnknownDiceTypeError):
    def __init__(self, dice_type: str, dice_roll: Union[int, str], message: str = ''):
        self.dice_roll = dice_roll
        super().__init__(dice_type, message)

    def __str__(self):
        return f'{self.dice_roll} has no value in {self.dice_type}. {self.message}'


class UnknownOperationError(Exception):
    def __init__(self, op: str, message: str = ''):
        self.op = op
        self.message = message
        super().__init__(self.message)

    def __str__(self):
        return f'{self.op} is not a supported operation. {self.message}'


class MissingOperandError(Exception):
    def __init__(self, op: str, message: str = ''):
        self.op = op
        self.message = message
        super().__init__(self.message)

    def __str__(self):
        return f'{self.op} requires an operand. {self.message}'


# -------------------------------------------------------------
#  Rolling Functions
# -------------------------------------------------------------

def get_operand(option_string: str) -> Tuple[str, str]:
    _, operand, option_string = operand_pattern.split(option_string, 1)
    return operand, option_string


def roll_dice(sides: int, num_dice: int):
    return [random.randint(0, sides - 1) for x in range(num_dice)]


def reroll_dice(roll_list, idx, sides):
    roll_list[idx] = random.randint(0, sides - 1)


def roll_command(command_str: str):
    math_strings = [x.strip() for x in supported_operators.split(command_str)]
    dice_strings = math_strings[::2]
    operator_strings = math_strings[1::2]
    operator_strings.insert(0, '+')

    rolls = []

    for dice_str in dice_strings:
        dice_roll_obj = DiceRoll(dice_str)
        rolls.append(dice_roll_obj)

    # Start resolving dice rolls
    total_sum = 0
    for op, roll in zip(operator_strings, rolls):
        if op == '+':
            total_sum += roll.sum
        elif op == '-':
            total_sum -= roll.sum
        else:
            raise UnknownOperationError(op, f'Used before {roll.dice_str}')

    result_dict = {
        'Total Sum': total_sum,
        'Rolls': [x.get_print_dict() for x in rolls],
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
