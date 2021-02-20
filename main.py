import discord
import json
import random
import re

from datetime import datetime
from typing import Optional, Tuple, List, Dict, Union
from pprint import pformat, pprint
from collections import defaultdict, Counter

_debug = True

_sep = '-'*40

client = discord.Client()
_dice_types: Optional[dict]

simple_numeric_pattern = re.compile(
    r'^\d+$'
)

base_roll_string = re.compile(
    r'(?P<num_dice>\d+)[dD](?P<dice_type>\d+|[A-Z]+)(?P<options>.*)'
)

# FIXME: Not Implemented
supported_comparisons = re.compile(
    r' (<(?!=)|>(?!=)|<=|>=) '
)

supported_operators = re.compile(
    r'([+-])'
)

_options = r'|'.join(
    [
        r'r'                # reroll
        r'k(?!l)',          # keep
        r'kl',              # keep low
        r'cs',              # critical success
        r'cf',              # critical failure
        r'!',               # explode
        r'(?<!~)<(?!=)',    # success-lt
        r'(?<!~)>(?!=)',    # success-gt
        r'(?<!~)<=',        # success-lte
        r'(?<!~)>=',        # success-gte
        r'(?<!~)==',        # success-eq
        r'~<(?!=)',         # failure-lt
        r'~>(?!=)',         # failure-gt
        r'~<=',             # failure-lte
        r'~>=',             # failure-gte
        r'~=',              # failure-eq
        r'b<(?!=)',         # boon-lt
        r'b>(?!=)',         # boon-gt
        r'b<=',             # boon-lte
        r'b>=',             # boon-gte
        r'b=',              # boon-eq
        r'c<(?!=)',         # complication-lt
        r'c>(?!=)',         # complication-gt
        r'c<=',             # complication-lte
        r'c>=',             # complication-gte
        r'c=',              # complication-eq
    ]
)

option_pattern = re.compile(
    r'(?P<op>' +
    _options +
    r')'
)

operand_pattern = re.compile(
    r'''^(\d+|(?:['"])\w+(?:['"]))?'''
)

# -------------------------------------------------------------
#  Dice Roll Class
# -------------------------------------------------------------


class Equation:
    def __init__(self, original_eq_str=''):
        self.original_equation_str = original_eq_str
        self.rolls: List[DiceRoll] = []
        self.ops = []

    @property
    def counters(self):
        counters = []
        for idx, roll in enumerate(self.rolls):
            if non_empty := roll.counter:
                counters.append((idx, non_empty))

        return counters if counters else None

    @property
    def sum(self):
        total_sum = 0
        for op, roll in zip(self.ops, self.rolls):
            if op == '+':
                total_sum += roll.sum
            elif op == '-':
                total_sum -= roll.sum
            else:
                raise UnknownOperationError(op, f'Used before {roll.dice_str}')
        return total_sum

    @property
    def successes(self):
        total_successes = 0
        total_failures = 0
        success_list = []
        failure_list = []
        net = 0
        valid = False
        for idx, roll in enumerate(self.rolls):
            try:
                total_successes += roll.successes
                success_list.append((idx, roll.successes))
                valid |= True
            except TypeError:
                pass

            try:
                total_failures += roll.failures
                failure_list.append((idx, roll.failures))
                valid |= True
            except TypeError:
                pass

        net = total_successes - total_failures

        result_dict = {
            'Success List': success_list,
            'Failure List': failure_list,
            'Successes': total_successes,
            'Failures': total_failures,
            'Net Successes': net,
        }

        return result_dict if valid else None

    @property
    def boons(self):
        total_boons = 0
        total_complications = 0
        net = 0
        valid = False
        for roll in self.rolls:
            try:
                total_boons += roll.boons
                valid |= True
            except TypeError:
                pass

            try:
                total_complications += roll.complications
                valid |= True
            except TypeError:
                pass

        net = total_boons - total_complications

        result_dict = {
            'Boons': total_boons,
            'Complications': total_complications,
            'Net Boons': net,
        }

        return result_dict if valid else None

    def get_print_dict(self):
        print_dict = {
            'Total Sum': self.sum,
            'Rolls': [x.get_print_dict() for x in self.rolls],
            'Successes': self.successes,
            'Boons': self.boons,
        }
        return print_dict

    def __repr__(self):
        print_dict = self.get_print_dict()
        return pformat(print_dict, indent=2)


class DiceRoll:
    def __init__(self, dice_str):
        # Store Dice String
        self.dice_str = dice_str

        # Prep independent attributes we'll need
        self.num_dice = 1
        self.dice_type = '1'
        self.roll_options_str = ''
        self.default_cmp = '>='

        # Setup dependent attributes
        self.sides = 0
        self.map = {}
        self.map_values = {}

        self.successes = None
        self.failures = None

        self.complications = None
        self.boons = None

        self.roll_history = None

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
    def roll_name(self):
        return f'{self.num_dice}d{self.dice_type}' if self.dice_type != '1' else f'{self.map[0]}'

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

    @property
    def counter(self):
        interesting_list = [r for r in self.faces if r in self.map_values.keys()]
        return Counter(interesting_list) if interesting_list else None

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

            try:
                dice_info = _dice_types[self.dice_type]
            except KeyError:
                simple_dice_match = simple_numeric_pattern.match(self.dice_type)
                # If it can, do it, otherwise try to load the dice info
                if simple_dice_match:
                    dice_info = {
                        'sides': int(simple_dice_match.group()),
                    }
                    if dice_info['sides'] < 1:
                        raise UnknownDiceTypeError(self.dice_type, "Illegal numeric dice!")
                else:
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
        threshold_option_found = False
        fail_threshold_option_found = False

        boon_threshold_found = False
        complication_threshold_found = False

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
            elif (op == '<' or op == '<=' or op == '>' or op == '>=' or op == '==') and not threshold_option_found:
                operand, option_string = get_operand(option_string)
                # Prep the success counter
                self.successes = 0
                if operand is None:
                    raise MissingOperandError('Compare', f'Used in {self.dice_str}')
                option_dict['threshold'] = int(operand)
                option_dict['compare'] = op
                threshold_option_found = True
            elif (op == '~<' or op == '~<=' or op == '~>' or op == '~>=' or op == '~=') and not fail_threshold_option_found:
                operand, option_string = get_operand(option_string)
                # Prep the success counter
                self.failures = 0
                if operand is None:
                    raise MissingOperandError('Compare', f'Used in {self.dice_str}')
                option_dict['fail_threshold'] = int(operand)
                option_dict['fail_compare'] = op
                threshold_option_found = True
            elif op == 'cs':
                operand, option_string = get_operand(option_string)
                if operand is None:
                    operand = self.map[self.sides-1]
                option_dict['crit'] = int(operand)
                # FIXME - Does it make _any_ sense to count doubles without a threshold?
                # if 'compare' not in option_dict:
                #     option_dict['threshold'] = int(operand)
                #     option_dict['compare'] = self.default_cmp
                #     self.successes = 0
            elif op == 'cf':
                operand, option_string = get_operand(option_string)
                if operand is None:
                    raise MissingOperandError('CriticalFailure', f'Used in {self.dice_str}')
                option_dict['crit_fail'] = int(operand)
            elif (op == 'c<' or op == 'c<=' or op == 'c>' or op == 'c>=' or op == 'c=') and \
                    not complication_threshold_found:
                operand, option_string = get_operand(option_string)
                # Prep the success counter
                self.complications = 0
                if operand is None:
                    raise MissingOperandError('Compare', f'Used in {self.dice_str}')
                option_dict['c_threshold'] = int(operand)
                option_dict['c_compare'] = op
                complication_threshold_found = True
            elif (op == 'b<' or op == 'b<=' or op == 'b>' or op == 'b>=' or op == 'b=') and \
                    not boon_threshold_found:
                operand, option_string = get_operand(option_string)
                # Prep the success counter
                self.boons = 0
                if operand is None:
                    raise MissingOperandError('Compare', f'Used in {self.dice_str}')
                option_dict['b_threshold'] = int(operand)
                option_dict['b_compare'] = op
                boon_threshold_found = True

        if _debug:
            pprint(option_dict, indent=2)

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
                print(f'Trying to explode {exp_dice}')
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

        # Now do "final roll" operations like keep
        if 'keep' in option_dict:
            self.roll_history = self.rolls[:]
            keep_num = option_dict['keep']
            self.rolls.sort()
            if keep_num > 0:
                self.rolls = self.rolls[:keep_num]
            elif keep_num < 0:
                self.rolls = self.rolls[keep_num:]
            else:
                self.rolls = []

        # Now calculate results
        if 'threshold' in option_dict:
            thresh = option_dict['threshold']
            op = option_dict['compare']
            dub_val = option_dict['crit'] if 'double' in option_dict else None
            for val in self.values:
                if op == '<':
                    mul = 2 * int(val <= dub_val) if dub_val is not None else 1
                    self.successes += int(val < thresh) * mul
                elif op == '>':
                    mul = 2 * int(val >= dub_val) if dub_val is not None else 1
                    self.successes += int(val > thresh) * mul
                elif op == '<=':
                    mul = 2 * int(val <= dub_val) if dub_val is not None else 1
                    self.successes += int(val <= thresh) * mul
                elif op == '>=':
                    mul = 2 * int(val >= dub_val) if dub_val is not None else 1
                    self.successes += int(val >= thresh) * mul
                elif op == '==':
                    mul = 2 * int(val == dub_val) if dub_val is not None else 1
                    self.successes += int(val == thresh) * mul

        # Now calculate results
        if 'fail_threshold' in option_dict:
            thresh = option_dict['fail_threshold']
            op = option_dict['fail_compare']
            dub_val = option_dict['crit_fail'] if 'crit_fail' in option_dict else None
            for val in self.values:
                if op == '~<':
                    mul = 2 * int(val <= dub_val) if dub_val is not None else 1
                    self.failures += int(val < thresh) * mul
                elif op == '~>':
                    mul = 2 * int(val >= dub_val) if dub_val is not None else 1
                    self.failures += int(val > thresh) * mul
                elif op == '~<=':
                    mul = 2 * int(val <= dub_val) if dub_val is not None else 1
                    self.failures += int(val <= thresh) * mul
                elif op == '~>=':
                    mul = 2 * int(val >= dub_val) if dub_val is not None else 1
                    self.failures += int(val >= thresh) * mul
                elif op == '~=':
                    mul = 2 * int(val == dub_val) if dub_val is not None else 1
                    self.failures += int(val == thresh) * mul

        # Now calculate complications
        if 'c_threshold' in option_dict:
            thresh = option_dict['c_threshold']
            op = option_dict['c_compare']
            for val in self.values:
                if op == 'c<':
                    self.complications += int(val < thresh)
                elif op == 'c>':
                    self.complications += int(val > thresh)
                elif op == 'c<=':
                    self.complications += int(val <= thresh)
                elif op == 'c>=':
                    self.complications += int(val >= thresh)
                elif op == 'c=':
                    self.complications += int(val == thresh)

        # Now calculate boons
        if 'b_threshold' in option_dict:
            thresh = option_dict['b_threshold']
            op = option_dict['b_compare']
            for val in self.values:
                if op == 'b<':
                    self.boons += int(val < thresh)
                elif op == 'b>':
                    self.boons += int(val > thresh)
                elif op == 'b<=':
                    self.boons += int(val <= thresh)
                elif op == 'b>=':
                    self.boons += int(val >= thresh)
                elif op == 'b=':
                    self.boons += int(val == thresh)

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

        if self.successes is not None:
            print_dict['Successes'] = self.successes

        if self.failures is not None:
            print_dict['Failures'] = self.failures

        if self.complications is not None:
            print_dict['Complications'] = self.complications

        if self.boons is not None:
            print_dict['Boons'] = self.boons

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
    # Toss quotes
    operand = operand.replace("'", '').replace('"', '')
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

    equation = Equation(command_str)
    equation.ops = operator_strings

    equation.rolls = []

    for dice_str in dice_strings:
        dice_roll_obj = DiceRoll(dice_str)
        equation.rolls.append(dice_roll_obj)

    return equation


# -------------------------------------------------------------
#  Actual Discord Bot
# -------------------------------------------------------------

def format_response(results: Equation):
    embed_dict = {
        'title': 'Roll Result',
        'type': 'rich',
        'timestamp': str(datetime.now()),
    }

    embed = discord.Embed.from_dict(embed_dict)

    skip_sum = False

    # ROLLS SECTION
    rolls_str = ''
    msg2 = ''
    for rolls in results.rolls:
        rolls_str += f'{rolls.roll_name}\n'
        if history := rolls.roll_history:
            rolls_str += '\n'
            msg2 += (
                '[ '
                + ' , '.join(map(str, [rolls.map[x] for x in history]))
                + ' ]\n'
            )
        msg2 += (
            '[ '
            + ' , '.join(map(str, rolls.faces))
            + ' ]\n'
        )

    embed.add_field(name='```Dice Rolls```', value=_sep, inline=False)
    embed.add_field(name='Dice', value=rolls_str)
    embed.add_field(name='Rolls', value=msg2)

    # COUNTER SECTION
    msg = ''
    msg2 = ''
    msg3 = ''
    if counters := results.counters:
        rolls = results.rolls
        embed.add_field(name='```Roll Stats```', value=_sep, inline=False)
        for idx, counter in counters:
            msg += f'{rolls[idx].num_dice}d{rolls[idx].dice_type}'
            for face, count in dict(counter).items():
                msg += '\n'
                msg2 += f'{face}\n'
                msg3 += f'{count}\n'
        embed.add_field(name='Roll', value=msg, inline=True)
        embed.add_field(name='Face', value=msg2, inline=True)
        embed.add_field(name='Count', value=msg3, inline=True)

    # SUCCESS SECTION
    if successes := results.successes:
        skip_sum = True
        msg1 = ''
        msg2 = ''
        msg3 = ''
        embed.add_field(
            name='```Successes and Failures```',
            value=_sep,
            inline=False,
        )

        msg1 += 'Successes'
        for idx, val in successes['Success List']:
            msg1 += '\n'
            msg2 += f'{results.rolls[idx].roll_name}\n'
            msg3 += f'{val}\n'
        if not successes['Success List']:
            msg1 += '\n'
            msg2 += '\n'
            msg3 += '0\n'

        msg1 += '*Subtotal*\n\n'
        msg2 += '\n\n'
        msg3 += f'{successes["Successes"]}\n\n'

        msg1 += 'Failures'
        for idx, val in successes['Failure List']:
            msg1 += '\n'
            msg2 += f'{results.rolls[idx].roll_name}\n'
            msg3 += f'{val}\n'
        if not successes['Failure List']:
            msg1 += '\n'
            msg2 += '\n'
            msg3 += '0\n'

        msg1 += '*Subtotal*\n\n'
        msg2 += '\n\n'
        msg3 += f'{successes["Failures"]}\n\n'

        net = successes["Net Successes"]
        abs_result = abs(net)

        msg1 += '**TOTAL**'
        msg2 += ''
        msg3 += f'**{abs_result} {"Successes" if net >= 0 else "Failures"}**'

        # pprint([msg1, msg2, msg3], indent=2)

        embed.add_field(name='Type', value=msg1, inline=True)
        embed.add_field(name='Roll', value=msg2, inline=True)
        embed.add_field(name='Value', value=msg3, inline=True)

    # BOON SECTION
    if boons := results.boons:
        skip_sum = True
        embed.add_field(
            name='```Boons and Complications```',
            value=_sep,
            inline=False,
        )
        net = boons["Net Boons"]
        abs_result = abs(net)
        msg = '\n'.join([
            '*Boons*',
            '*Complications*',
            '\n**Result**'
        ])

        msg2 = '\n'.join([
            f'{boons["Boons"]}',
            f'{boons["Complications"]}',
            f'\n**{abs_result} {"Boons" if net >= 0 else "Complications"}**'
        ])
        embed.add_field(name='Key', value=msg, inline=True)
        embed.add_field(name='Value', value=msg2, inline=True)

    # SUM SECTION
    if not skip_sum:
        rolls_str_2 = ''
        msg2 = ''
        for idx, rolls in enumerate(results.rolls):
            rolls_str_2 += f'{rolls.roll_name}\n'
            msg2 += f'{results.ops[idx]}{rolls.sum}\n'

        rolls_str_2 += "\n**Total**"
        msg2 += f'\n**{results.sum}**'

        embed.add_field(name='```Sum```', value=_sep, inline=False)
        embed.add_field(name='Dice', value=rolls_str_2)
        embed.add_field(name='Rolls', value=msg2)

    # pprint(embed.fields, indent=2)

    return embed

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
        response = format_response(results)
        await message.channel.send(None, embed=response)


if __name__ == '__main__':
    with open('env.json', 'r') as env_file:
        env = json.load(env_file)

    with open('dice.json', 'r') as dice_file:
        _dice_types = json.load(dice_file)

    discord_token = env['TOKEN']
    random.seed()
    client.run(discord_token)
