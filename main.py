import discord
import json
import random
import re

from datetime import datetime
from typing import Optional, Tuple, List, Dict, Union, Any
from pprint import pformat, pprint
from collections import defaultdict, Counter, namedtuple

# FIXME:
#           4: No support for "repeat"
#           5: Parentheses? Do these affect only sums?

_debug = False

_sep = '-' * 80

client = discord.Client()
_dice_types: Optional[dict]

comment_pattern = re.compile(
    r'#(?P<comment>.*$)'
)

simple_numeric_pattern = re.compile(
    r'^\d+$'
)

# base_roll_string = re.compile(
#     r'(?P<num_dice>\d+)[dD](?P<dice_type>\d+|[A-Z]+)(?P<options>.*)'
# )

supported_comparisons = re.compile(
    r' +(?P<compare><(?!=)|>(?!=)|<=|>=) *(?P<cmp_val>\d+)$'
)

supported_operators = re.compile(
    r'([+-])'
)

_options = r'|'.join(
    [
        r'r',  # reroll
        r'k(?!l)',  # keep
        r'kl',  # keep low
        r'cs',  # critical success
        r'cf',  # critical failure
        r'cb',  # critical boon
        r'cx',  # critical complication
        r'!',  # explode
        r'(?<!~)<(?!=)',  # success-lt
        r'(?<!~)>(?!=)',  # success-gt
        r'(?<!~)<=',  # success-lte
        r'(?<!~)>=',  # success-gte
        r'(?<!~)==',  # success-eq
        r'~<(?!=)',  # failure-lt
        r'~>(?!=)',  # failure-gt
        r'~<=',  # failure-lte
        r'~>=',  # failure-gte
        r'~=',  # failure-eq
        r'b<(?!=)',  # boon-lt
        r'b>(?!=)',  # boon-gt
        r'b<=',  # boon-lte
        r'b>=',  # boon-gte
        r'b=',  # boon-eq
        r'x<(?!=)',  # complication-lt
        r'x>(?!=)',  # complication-gt
        r'x<=',  # complication-lte
        r'x>=',  # complication-gte
        r'x=',  # complication-eq
        r'x(?![=<>])',  # natural-complication
        r'b(?![=<>])',  # natural
    ]
)

option_pattern = re.compile(
    r'(?P<op>' +
    _options +
    r')'
)

operand_pattern = re.compile(
     r'''^((?:(?:\d+|(?:['"])\w+(?:['"])),?)*)'''
)

# -------------------------------------------------------------
#  Dice Roll Class
# -------------------------------------------------------------

RollResult = namedtuple('RollResult', ['total', 'map'])


class Equation:
    def __init__(self, original_eq_str=''):
        self.original_equation_str = original_eq_str
        self.rolls: List[DiceRoll] = []
        self.ops = []
        self.final_compare = None
        self.final_compare_val = None

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
        sum_exists = False
        for op, roll in zip(self.ops, self.rolls):
            sum_exists |= roll.sum is not None
            if op == '+':
                total_sum += roll.sum if roll.sum else 0
            elif op == '-':
                total_sum -= roll.sum if roll.sum else 0
            else:
                raise UnknownOperationError(op, f'Used before {roll.dice_str}')
        return total_sum

    @property
    def successes(self):
        total_successes = 0
        success_list = []
        valid = False

        for idx, roll in enumerate(self.rolls):
            try:
                total_successes += roll.successes
                success_list.append((idx, roll.successes))
                valid |= True
            except TypeError:
                pass

        result = RollResult(
            total=total_successes,
            map=success_list,
        )

        return result if valid else None

    @property
    def failures(self):
        total_failures = 0
        failure_list = []
        valid = False

        for idx, roll in enumerate(self.rolls):
            try:
                total_failures += roll.failures
                failure_list.append((idx, roll.failures))
                valid |= True
            except TypeError:
                pass

        result = RollResult(
            total=total_failures,
            map=failure_list,
        )

        return result if valid else None

    @property
    def boons(self):
        total_boons = 0
        boon_list = []
        valid = False

        for idx, roll in enumerate(self.rolls):
            try:
                total_boons += roll.boons
                boon_list.append((idx, roll.boons))
                valid |= True
            except TypeError:
                pass

        result = RollResult(
            total=total_boons,
            map=boon_list,
        )

        return result if valid else None

    @property
    def complications(self):
        total_complications = 0
        complication_list = []
        valid = False

        for idx, roll in enumerate(self.rolls):
            try:
                total_complications += roll.complications
                complication_list.append((idx, roll.complications))
                valid |= True
            except TypeError:
                pass

        result = RollResult(
            total=total_complications,
            map=complication_list,
        )

        return result if valid else None

    @property
    def final_compare_result(self):
        result = None
        if self.final_compare is not None and self.final_compare_val is not None:
            if self.final_compare == '<':
                result = self.sum < self.final_compare_val
            elif self.final_compare == '>':
                result = self.sum > self.final_compare_val
            elif self.final_compare == '<=':
                result = self.sum <= self.final_compare_val
            elif self.final_compare == '>=':
                result = self.sum >= self.final_compare_val
        return result

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
        self.face_names = {}
        self.map_values = {}

        self.successes = None
        self.failures = None

        self.complications = None
        self.boons = None

        self.natural_success = None
        self.natural_success_compare = None

        self.natural_fail = None
        self.natural_fail_compare = None

        self.natural_complication = None
        self.natural_c_compare = None

        self.natural_boon = None
        self.natural_b_compare = None

        self.natural_cs = None

        self.natural_cf = None

        self._roll_history = []

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
    def roll_history(self):
        return self._roll_history[0] if self._roll_history else None

    def push_history(self):
        self._roll_history.append(self.rolls[:])

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
                    values = None
                    break
        return values

    @property
    def sum(self):
        return sum(self.values) if self.values else None

    @property
    def counter(self):
        interesting_list = [r for r in self.faces if r in self.map_values.keys() or r in self.face_names.keys()]
        return Counter(interesting_list) if interesting_list else None

    def _decode_dice_string(self):
        dice_info: Dict[str, Any]
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
                dice_info = _dice_types[self.dice_type.upper()]
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

        dice_map = dice_info['map'] if 'map' in dice_info else range(1, int(dice_info['sides']) + 1)
        self.map = [str(entry) for entry in dice_map]
        self.face_names = dice_info['names'] if 'names' in dice_info else {}
        self.sides = dice_info['sides'] if 'sides' in dice_info else len(self.map)
        self.map_values = dice_info['value'] if 'value' in dice_info else {}

        self.natural_success = dice_info['success'] if 'success' in dice_info else None
        self.natural_success_compare = '=' + dice_info['success_op'] if 'success_op' in dice_info else None

        self.natural_fail = dice_info['fail'] if 'fail' in dice_info else None
        self.natural_fail_compare = '~' + dice_info['fail_op'] if 'fail_op' in dice_info else None

        self.natural_complication = dice_info['complication'] if 'complication' in dice_info else None
        self.natural_c_compare = 'x' + dice_info['complication_op'] if 'complication_op' in dice_info else None

        self.natural_boon = dice_info['boon'] if 'boon' in dice_info else None
        self.natural_b_compare = 'b' + dice_info['boon_op'] if 'boon_op' in dice_info else None

        self.natural_cs = dice_info['crit_success'] if 'crit_success' in dice_info else None
        self.natural_cf = dice_info['crit_fail'] if 'crit_fail' in dice_info else None
        self.natural_cb = dice_info['crit_boon'] if 'crit_boon' in dice_info else None
        self.natural_cc = dice_info['crit_complication'] if 'crit_complication' in dice_info else None

    def _parse_options(self):
        option_dict: Dict[str, Union[str, int, set]]
        option_dict = defaultdict(set)

        option_string = self.roll_options_str[:]

        keep_option_found = False
        threshold_option_found = False
        fail_threshold_option_found = False

        boon_threshold_found = False
        complication_threshold_found = False

        # ------------------------------
        #  Preset Natural Options
        # ------------------------------
        if self.natural_success:
            self.successes = 0
            option_dict['threshold'] = self.natural_success
            option_dict['compare'] = self.natural_success_compare
        if self.natural_cs:
            option_dict['crit'] = self.natural_cs
        if self.natural_fail:
            self.failures = 0
            option_dict['fail_threshold'] = self.natural_fail
            option_dict['fail_compare'] = self.natural_fail_compare
        if self.natural_cf:
            option_dict['crit_fail'] = self.natural_cf
        if self.natural_boon:
            self.boons = 0
            option_dict['b_threshold'] = self.natural_boon
            option_dict['b_compare'] = self.natural_b_compare
        if self.natural_cb:
            option_dict['b_crit'] = self.natural_cb
        if self.natural_complication:
            self.complications = 0
            option_dict['c_threshold'] = self.natural_complication
            option_dict['c_compare'] = self.natural_c_compare
        if self.natural_cc:
            option_dict['c_crit'] = self.natural_cc

        if _debug:
            print(f'Parsing {option_string}')

        while option_string:
            # Note: _ had better be empty....
            # print(option_string)
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
                if operand is None:
                    raise MissingOperandError('Explode', f'Used in {self.dice_str}')
                try:
                    option_dict['explode'].add(str(int(operand)))
                except ValueError:
                    option_dict['explode'] |= form_face_roll_list(operand)
            elif op == 'r':
                operand, option_string = get_operand(option_string)
                if operand is None:
                    raise MissingOperandError('Reroll', f'Used in {self.dice_str}')
                try:
                    option_dict['reroll'].add(str(int(operand)))
                except ValueError:
                    option_dict['reroll'] |= form_face_roll_list(operand)
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
            elif (op == '<' or op == '<=' or op == '>' or op == '>=') and not threshold_option_found:
                operand, option_string = get_operand(option_string)
                # Prep the success counter
                self.successes = 0
                if operand is None:
                    raise MissingOperandError('Compare', f'Used in {self.dice_str}')
                option_dict['threshold'] = int(operand)
                option_dict['compare'] = op
                threshold_option_found = True
            elif (
                    op == '~<' or op == '~<=' or op == '~>' or op == '~>=') \
                    and not fail_threshold_option_found:
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
                    raise MissingOperandError('Critical Success', f'Used in {self.dice_str}')
                try:
                    option_dict['crit'] = int(operand)
                except ValueError:
                    option_dict['crit'] |= form_roll_list(operand)
            elif op == 'cf':
                operand, option_string = get_operand(option_string)
                if operand is None:
                    raise MissingOperandError('CriticalFailure', f'Used in {self.dice_str}')
                try:
                    option_dict['crit_fail'] = int(operand)
                except ValueError:
                    option_dict['crit_fail'] |= form_roll_list(operand)
            elif op == 'cb':
                operand, option_string = get_operand(option_string)
                if operand is None:
                    raise MissingOperandError('CriticalBoon', f'Used in {self.dice_str}')
                try:
                    option_dict['b_crit'] = int(operand)
                except ValueError:
                    option_dict['b_crit'] |= form_roll_list(operand)
            elif op == 'cx':
                operand, option_string = get_operand(option_string)
                if operand is None:
                    raise MissingOperandError('CriticalComplication', f'Used in {self.dice_str}')
                try:
                    option_dict['c_crit'] = int(operand)
                except ValueError:
                    option_dict['c_crit'] |= form_roll_list(operand)
            elif (op == 'x<' or op == 'x<=' or op == 'x>' or op == 'x>=') and \
                    not complication_threshold_found:
                operand, option_string = get_operand(option_string)
                # Prep the success counter
                self.complications = 0
                if operand is None:
                    raise MissingOperandError('Compare', f'Used in {self.dice_str}')
                option_dict['c_threshold'] = int(operand)
                option_dict['c_compare'] = op
                complication_threshold_found = True
            elif (op == 'b<' or op == 'b<=' or op == 'b>' or op == 'b>=') and \
                    not boon_threshold_found:
                operand, option_string = get_operand(option_string)
                # Prep the success counter
                self.boons = 0
                if operand is None:
                    raise MissingOperandError('Compare', f'Used in {self.dice_str}')
                option_dict['b_threshold'] = int(operand)
                option_dict['b_compare'] = op
                boon_threshold_found = True
            elif op == 'x' and not complication_threshold_found:
                self.complications = 0
                operand, option_string = get_operand(option_string)
                if operand is None:
                    raise MissingOperandError('Complication', f'Used in {self.dice_str}')
                option_dict['c_threshold'] = int(operand)
                option_dict['c_compare'] = self.natural_c_compare
            elif op == 'b' and not boon_threshold_found:
                self.boons = 0
                operand, option_string = get_operand(option_string)
                if operand is None:
                    raise MissingOperandError('Boon', f'Used in {self.dice_str}')
                option_dict['b_threshold'] = int(operand)
                option_dict['b_compare'] = self.natural_c_compare
            elif op == '==' and not threshold_option_found:
                self.successes = 0
                operand, option_string = get_operand(option_string)
                if operand is None:
                    raise MissingOperandError('Success', f'Used in {self.dice_str}')
                option_dict['threshold'] |= form_roll_list(operand)
                option_dict['compare'] = op
            elif op == '~=' and not fail_threshold_option_found:
                self.failures = 0
                operand, option_string = get_operand(option_string)
                if operand is None:
                    raise MissingOperandError('Failure', f'Used in {self.dice_str}')
                option_dict['fail_threshold'] |= form_roll_list(operand)
                option_dict['fail_compare'] = op
            elif op == 'b=' and not threshold_option_found:
                self.boons = 0
                operand, option_string = get_operand(option_string)
                if operand is None:
                    raise MissingOperandError('Boon', f'Used in {self.dice_str}')
                option_dict['b_threshold'] |= form_roll_list(operand)
                option_dict['b_compare'] = op
            elif op == 'x=' and not threshold_option_found:
                self.complications = 0
                operand, option_string = get_operand(option_string)
                if operand is None:
                    raise MissingOperandError('Complication', f'Used in {self.dice_str}')
                option_dict['c_threshold'] |= form_roll_list(operand)
                option_dict['c_compare'] = op

        if _debug:
            pprint(option_dict, indent=2)

        self._resolve_options(option_dict)

    def _resolve_options(self, option_dict: dict):

        # Reroll any initial dice
        for idx, face in enumerate(self.faces):
            if face in option_dict['reroll']:
                self.push_history()
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
            if _debug:
                pprint(c)
            new_rolls = []
            # Explode dice
            for exp_dice in option_dict['explode']:
                if _debug:
                    print(f'Trying to explode {exp_dice}: x{c[exp_dice]}')
                new_rolls.extend(roll_dice(self.sides, c[exp_dice]))

            new_face_list = [self.map[roll] for roll in new_rolls]

            if _debug:
                print(new_face_list)

            if new_face_list:
                self.push_history()

            if _debug:
                print(f'New Rolls: {new_rolls}')
                print(f'New Faces: {new_face_list}')

            # Reroll them if needed
            for idx, face in enumerate(new_face_list):
                if face in option_dict['reroll']:
                    self.push_history()
                    reroll_dice(new_rolls, idx, self.sides)
                    if _debug:
                        print(f'Rerolled #{idx}: {new_rolls[idx]}')

            # Append the new rolls
            self.rolls.extend(new_rolls)
            face_list = [self.map[roll] for roll in new_rolls]

        # Now do "final roll" operations like keep
        if 'keep' in option_dict:
            self.push_history()
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
            dub_val = option_dict['crit'] if 'crit' in option_dict else None
            for face in self.faces:
                if op == '==':
                    mul = 1 + int(face in dub_val) if dub_val is not None else 1
                    self.successes += int(face in thresh) * mul
                else:
                    try:
                        val = self.map_values[face]
                    except KeyError:
                        val = int(face)
                    if op == '<':
                        mul = 1 + int(val <= dub_val) if dub_val is not None else 1
                        self.successes += int(val < thresh) * mul
                    elif op == '>':
                        mul = 1 + int(val >= dub_val) if dub_val is not None else 1
                        self.successes += int(val > thresh) * mul
                    elif op == '<=':
                        mul = 1 + int(val <= dub_val) if dub_val is not None else 1
                        self.successes += int(val <= thresh) * mul
                    elif op == '>=':
                        mul = 1 + int(val >= dub_val) if dub_val is not None else 1
                        self.successes += int(val >= thresh) * mul

        # Now calculate results
        if 'fail_threshold' in option_dict:
            thresh = option_dict['fail_threshold']
            op = option_dict['fail_compare']
            dub_val = option_dict['crit_fail'] if 'crit_fail' in option_dict else None
            for face in self.faces:
                if op == '~=':
                    mul = 1 + int(face in dub_val) if dub_val is not None else 1
                    self.failures += int(face in thresh) * mul
                else:
                    try:
                        val = self.map_values[face]
                    except KeyError:
                        val = int(face)
                    if op == '~<':
                        mul = 1 + int(val <= dub_val) if dub_val is not None else 1
                        self.failures += int(val < thresh) * mul
                    elif op == '~>':
                        mul = 1 + int(val >= dub_val) if dub_val is not None else 1
                        self.failures += int(val > thresh) * mul
                    elif op == '~<=':
                        mul = 1 + int(val <= dub_val) if dub_val is not None else 1
                        self.failures += int(val <= thresh) * mul
                    elif op == '~>=':
                        mul = 1 + int(val >= dub_val) if dub_val is not None else 1
                        self.failures += int(val >= thresh) * mul

        # Now calculate complications
        if 'c_threshold' in option_dict:
            thresh = option_dict['c_threshold']
            op = option_dict['c_compare']
            dub_val = option_dict['c_crit'] if 'c_crit' in option_dict else None
            for face in self.faces:
                if op == 'x=':
                    mul = 1 + int(face in dub_val) if dub_val is not None else 1
                    self.complications += int(face in thresh) * mul
                else:
                    try:
                        val = self.map_values[face]
                    except KeyError:
                        val = int(face)
                    if op == 'x<':
                        mul = 1 + int(val <= dub_val) if dub_val is not None else 1
                        self.complications += int(val < thresh) * mul
                    elif op == 'x>':
                        mul = 1 + int(val >= dub_val) if dub_val is not None else 1
                        self.complications += int(val > thresh) * mul
                    elif op == 'x<=':
                        mul = 1 + int(val <= dub_val) if dub_val is not None else 1
                        self.complications += int(val <= thresh) * mul
                    elif op == 'x>=':
                        mul = 1 + int(val >= dub_val) if dub_val is not None else 1
                        self.complications += int(val >= thresh) * mul

        # Now calculate boons
        if 'b_threshold' in option_dict:
            thresh = option_dict['b_threshold']
            op = option_dict['b_compare']
            dub_val = option_dict['b_crit'] if 'b_crit' in option_dict else None
            for face in self.faces:
                if op == 'b=':
                    mul = 1 + int(face in dub_val) if dub_val is not None else 1
                    self.boons += int(face in thresh) * mul
                else:
                    try:
                        val = self.map_values[face]
                    except KeyError:
                        val = int(face)
                    if op == 'b<':
                        mul = 1 + int(val <= dub_val) if dub_val is not None else 1
                        self.boons += int(val < thresh) * mul
                    elif op == 'b>':
                        mul = 1 + int(val >= dub_val) if dub_val is not None else 1
                        self.boons += int(val > thresh) * mul
                    elif op == 'b<=':
                        mul = 1 + int(val <= dub_val) if dub_val is not None else 1
                        self.boons += int(val <= thresh) * mul
                    elif op == 'b>=':
                        mul = 1 + int(val >= dub_val) if dub_val is not None else 1
                        self.boons += int(val >= thresh) * mul

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

def form_roll_list(operand_str):
    operand_set = set()
    operand_str_list = operand_str.split(',')
    for op in operand_str_list:
        try:
            operand_set.add(int(op))
        except ValueError:
            operand_set.add(op)

    return operand_set


def form_face_roll_list(operand_str):
    operand_set = set(operand_str.split(','))
    return operand_set


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
    # Find the one comparison operator supported
    command_str, cmp_op, cmp_val, _ = supported_comparisons.split(command_str, 1)

    # Find the math parts
    math_strings = [x.strip() for x in supported_operators.split(command_str)]
    dice_strings = math_strings[::2]
    operator_strings = math_strings[1::2]
    # Fix the zero'th entry to be a sum, which aligns the entries
    operator_strings.insert(0, '+')

    # Create the Equation that will do the math
    equation = Equation(command_str)
    equation.ops = operator_strings
    equation.final_compare = cmp_op
    equation.final_compare_val = int(cmp_val)

    equation.rolls = []

    # Creating the roll objects actually rolls the dice
    for dice_str in dice_strings:
        dice_roll_obj = DiceRoll(dice_str)
        equation.rolls.append(dice_roll_obj)

    return equation


# -------------------------------------------------------------
#  Actual Discord Bot
# -------------------------------------------------------------

def format_response(results: Equation):
    embed_dict = {
        # 'title': 'Roll Result',
        'type': 'rich',
        # 'timestamp': str(datetime.now()),
    }

    embed = discord.Embed.from_dict(embed_dict)

    skip_sum = False

    sfbc_str = ''
    sf_flag = False
    bc_flag = False
    sf_net = 0
    bc_net = 0

    if successes := results.successes:
        sf_flag = True
        sf_net += successes.total

    if failures := results.failures:
        sf_flag = True
        sf_net -= failures.total

    if boons := results.boons:
        bc_flag = True
        bc_net += boons.total

    if complications := results.complications:
        bc_flag = True
        bc_net -= complications.total

    if sf_flag or bc_flag:
        skip_sum = True
        if sf_flag:
            abs_result = abs(sf_net)
            if sf_net == 0:
                sf_type = ''
            elif sf_net > 0:
                sf_type = 'Success' if sf_net == 1 else 'Successes'
            else:
                sf_type = 'Failure' if sf_net == -1 else 'Failures'
            if sf_type:
                sfbc_str += f'{abs_result} {sf_type}\n'

        if bc_flag:
            abs_result = abs(bc_net)
            if bc_net == 0:
                bc_type = ''
            elif bc_net > 0:
                bc_type = 'Boon' if bc_net == 1 else 'Boons'
            else:
                bc_type = 'Complication' if bc_net == -1 else 'Complications'
            if bc_type:
                sfbc_str += f'{abs_result} {bc_type}\n'

    rolls_str = '```\n'
    msg2 = '```'

    long_rolls_flag = False
    sum_exists_flag = False
    for idx, rolls in enumerate(results.rolls):
        sign = results.ops[idx].replace('+', '')
        sum_exists_flag |= rolls.sum is not None
        sum_str = f'  =  {sign}{rolls.sum}' if (not skip_sum and rolls.sum is not None) else ''
        rolls_str += f'{rolls.roll_name}\n'
        long_rolls_flag = len(rolls.rolls) > 6
        if history := rolls.roll_history:
            rolls_str += '\n'
            msg2 += (
                    '[ '
                    + ', '.join(map(str, [rolls.map[x] for x in history]))
                    + ' ]\n'
            )
        msg2 += (
                '[ '
                + ', '.join(map(str, rolls.faces))
                + f' ]{sum_str}\n'
        )
    # Skip sums when there are no sums...
    # print(f'SKIP: {skip_sum}     Exists: {sum_exists_flag}')
    total_str = f'{results.sum}\n'
    skip_sum = (skip_sum | (not sum_exists_flag)) & (results.final_compare_result is None)
    if len(results.rolls) > 1 and not skip_sum:
        rolls_str += 'TOTAL\n'
        msg2 += total_str
        if results.final_compare_result is not None:
            rolls_str += f'{results.sum} {results.final_compare} {results.final_compare_val}\n'
            msg2 += "SUCCESS" if results.final_compare_result else "FAIL"
    rolls_str += '```'
    msg2 += '```'
    # embed.add_field(name='```Dice Rolls```', value=_sep, inline=False)
    if len(results.rolls) > 1:
        embed.add_field(name='Dice', value=rolls_str)
    embed.add_field(name='Rolls', value=msg2)

    # COUNTER SECTION
    msg = ''
    if counters := results.counters:
        face_lengths = []
        for roll in results.rolls:
            face_lengths.extend([len(name) for f, name in roll.face_names.items() if f in roll.faces])
        max_face_len = max(max(face_lengths)+1, 10)
        rolls = results.rolls
        msg += '```\n'
        total_counter = Counter()
        for idx, counter in counters:
            rollname = f'{rolls[idx].num_dice}d{rolls[idx].dice_type}'
            for face, count in dict(counter).items():
                face_name = rolls[idx].face_names[face]
                msg += f'{rollname:<8} {face_name:<{max_face_len}} {count}\n'
                rollname = ''
                total_counter.update([face_name for x in range(count)])

        rollname = 'TOTAL'
        if len(results.rolls) > 1:
            for face, count in dict(total_counter).items():
                msg += f'{rollname:<8} {face:<{max_face_len}} {count}\n'
                rollname = ''

        msg += '```'
        if _debug:
            print(msg)
        embed.add_field(name='Roll Stats', value=msg, inline=False)

    if sfbc_str:
        sfbc_str = '```\n' + sfbc_str + '```'
        embed.add_field(name='Results', value=sfbc_str, inline=False)

    return embed


def format_response_full(results: Equation):
    embed_dict = {
        'title': 'Roll Result',
        'type': 'rich',
        'timestamp': str(datetime.now()),
    }

    embed = discord.Embed.from_dict(embed_dict)

    skip_sum = False

    # ROLLS SECTION
    rolls_str = '```\n'
    msg2 = '```\n'
    for rolls in results.rolls:
        rolls_str += f'{rolls.roll_name}\n'
        if history := rolls.roll_history:
            rolls_str += '\n'
            msg2 += (
                    '[ '
                    + ', '.join(map(str, [rolls.map[x] for x in history]))
                    + ' ]\n'
            )
        msg2 += (
                '[ '
                + ', '.join(map(str, rolls.faces))
                + ' ]\n'
        )

    rolls_str += '```\n'
    msg2 += '```\n'

    embed.add_field(name='Dice Rolls', value=_sep, inline=False)
    if len(results.rolls) > 1:
        embed.add_field(name='Dice', value=rolls_str)
    embed.add_field(name='Rolls', value=msg2)

    # COUNTER SECTION
    msg = ''
    if counters := results.counters:
        face_lengths = []
        for roll in results.rolls:
            face_lengths.extend([len(name) for f, name in roll.face_names.items() if f in roll.faces])
        max_face_len = max(max(face_lengths)+1, 10)
        embed.add_field(name='Roll Stats', value=_sep, inline=False)
        rolls = results.rolls
        msg += '```\n'
        total_counter = Counter()
        for idx, counter in counters:
            rollname = f'{rolls[idx].num_dice}d{rolls[idx].dice_type}'
            for face, count in dict(counter).items():
                face_name = rolls[idx].face_names[face]
                msg += f'{rollname:<8} {face_name:<{max_face_len}} {count}\n'
                rollname = ''
                total_counter.update([face_name for x in range(count)])

        rollname = 'Total'
        if len(results.rolls) > 1:
            for face, count in dict(total_counter).items():
                msg += f'\n{rollname:<8} {face:<{max_face_len}} {count}'
                rollname = ''

        msg += '```'
        print(msg)
        embed.add_field(name='Roll Stats', value=msg, inline=False)

    # SUCCESS SECTION
    msg1 = '```\n'
    msg2 = '```\n'
    msg3 = '```\n'
    net = 0
    sf_section_flag = False

    if successes := results.successes:
        sf_section_flag = True
        net += successes.total
        msg1 += 'Successes'
        for idx, val in successes.map:
            msg1 += '\n'
            msg2 += f'{results.rolls[idx].roll_name}\n'
            msg3 += f'{val}\n'
        if not successes.map:
            msg1 += '\n'
            msg2 += '\n'
            msg3 += '0\n'

        if len(results.rolls) > 1:
            msg1 += '\n\n'
            msg2 += 'Subtotal\n\n'
            msg3 += f'{successes.total}\n\n'

    if failures := results.failures:
        net -= failures.total
        sf_section_flag = True
        msg1 += 'Failures'
        for idx, val in failures.map:
            msg1 += '\n'
            msg2 += f'{results.rolls[idx].roll_name}\n'
            msg3 += f'{val}\n'
        if not failures.map:
            msg1 += '\n'
            msg2 += '\n'
            msg3 += '0\n'

        if len(results.rolls) > 1:
            msg1 += '\n\n'
            msg2 += 'Subtotal\n\n'
            msg3 += f'{failures.total}\n\n'

    if sf_section_flag:
        abs_result = abs(net)

        if net == 0:
            sf_type = ''
        elif net > 0:
            sf_type = 'Success' if net == 1 else 'Successes'
        else:
            sf_type = 'Failure' if net == -1 else 'Failures'

        msg1 += 'TOTAL'
        msg2 += ''
        msg3 += f'{abs_result} {sf_type}'

        msg1 += '```'
        msg2 += '```'
        msg3 += '```'

        skip_sum = True

        embed.add_field(
            name='Successes and Failures',
            value=_sep,
            inline=False,
        )

        embed.add_field(name='Type', value=msg1, inline=True)
        embed.add_field(name='Roll', value=msg2, inline=True)
        embed.add_field(name='Value', value=msg3, inline=True)

    # BOON SECTION
    msg1 = '```\n'
    msg2 = '```\n'
    msg3 = '```\n'
    net = 0
    bc_section_flag = False

    if boons := results.boons:
        bc_section_flag = True
        net += boons.total
        msg1 += 'Boons'
        for idx, val in boons.map:
            msg1 += '\n'
            msg2 += f'{results.rolls[idx].roll_name}\n'
            msg3 += f'{val}\n'
        if not boons.map:
            msg1 += '\n'
            msg2 += '\n'
            msg3 += '0\n'

        if len(results.rolls) > 1:
            msg1 += '\n\n'
            msg2 += 'Subtotal\n\n'
            msg3 += f'{boons.total}\n\n'

    if complications := results.complications:
        net -= complications.total
        bc_section_flag = True
        msg1 += 'Complications'
        for idx, val in complications.map:
            msg1 += '\n'
            msg2 += f'{results.rolls[idx].roll_name}\n'
            msg3 += f'{val}\n'
        if not complications.map:
            msg1 += '\n'
            msg2 += '\n'
            msg3 += '0\n'

        if len(results.rolls) > 1:
            msg1 += '\n\n'
            msg2 += 'Subtotal\n\n'
            msg3 += f'{complications.total}\n\n'

    if bc_section_flag:
        abs_result = abs(net)

        if net == 0:
            bc_type = ''
        elif net > 0:
            bc_type = 'Boon' if net == 1 else 'Boons'
        else:
            bc_type = 'Complication' if net == -1 else 'Complications'

        msg1 += 'TOTAL'
        msg2 += ''
        msg3 += f'{abs_result} {bc_type}'

        msg1 += '```\n'
        msg2 += '```\n'
        msg3 += '```\n'

        skip_sum = True

        embed.add_field(
            name='Boons and Complications',
            value=_sep,
            inline=False,
        )

        embed.add_field(name='Type', value=msg1, inline=True)
        embed.add_field(name='Roll', value=msg2, inline=True)
        embed.add_field(name='Value', value=msg3, inline=True)

    # SUM SECTION
    if not skip_sum:
        rolls_str_2 = '```\n'
        msg2 = '```\n'
        if len(results.rolls) > 1:
            for idx, rolls in enumerate(results.rolls):
                if rolls.sum:
                    rolls_str_2 += f'{rolls.roll_name}\n'
                    sign = results.ops[idx].replace('+', '')
                    msg2 += f'{sign}{rolls.sum}\n'

        rolls_str_2 += "\nTotal\n```"
        msg2 += f'\n{results.sum}\n```'

        embed.add_field(name='Sum', value=_sep, inline=False)
        embed.add_field(name='Dice', value=rolls_str_2)
        embed.add_field(name='Rolls', value=msg2)

    # pprint(embed.fields, indent=2)

    return embed


def create_help():
    cmd = (
        '''```
r         Simple Roll
rf        Verbose (Full) Roll
h         Help
dice [X]  List dice names | List info for dice X

Roll Syntax:
    #dDICE      Roll # of DICE (case insensitive)
    
            2d20    : 2x 20 sided dice
            10dCOIN : 10x coins (H or T)
            4d6     : 4x  6 sided dice
            2dST    : 2x Star Trek Adventure Dice
            1dDD    : 1x D&D Dice
            
    1d20[options]   Apply roll options to a single d20.
                    There must not be any spaces between options
                
    4d6 + 2d4      Roll multiple dice and sum the results
    1d20 + 2       Add a constant 2 to the result of a d20
    1d20 - 4 < 10  Compare the sum of all rolls and math
    
    Math and dice roll options can be combined:
    
        2d20kl1 + 2 : Roll 2d20, keep the lowest, add 2
        4dC + 4dC   : Roll 4 Challenge dice twice, show their
                      results separately
```'''
    )

    extra_rolls = (
        '''```
r#      Reroll #. Can be specified multiple times
            r1r2 : reroll 1's and 2's
            r"H" : reroll die face called H (must be quoted)
!#      Explode dice that roll #
            !10  : Explode 10's
            !"E" : Explode dice that roll the face called E 
                   (must be quoted)
k#      Keep # highest dice
            k1   : keep highest dice (D&D Advantage)
            k2   : keep 2 highest dice
kl#     Keep # lowest dice
            kl1  : keep lowest dice (D&D Disadvantage)
            kl4  : keep 4 lowest dice
```'''
    )

    success = (
        '''```
[cmp]#  Success comparison
            <#  : lt
            <=# : lte
            >#  : gt
            >=# : gte
            ==# : eq
            
~[cmp]# Failure comparison
            ~<#  : lt
            ~<=# : lte
            ~>#  : gt
            ~>=# : gte
            ~=# : eq

cs#     Critical success (2x) when the dice rolls:
            > | >= : at least #
            < | <= ; at most #
            (A success criteria must be specified)
            
cf#     Critical failure (2x) when the dice rolls:
            ~> | ~>= : at least #
            ~< | ~<= ; at most #
            (A failure criteria must be specified)
```'''
    )

    boon = (
        '''```
b[cmp]# Boon comparisons start with 'b'
            b<#  : lt
            b<=# : lte
            b>#  : gt
            b>=# : gte
            b=#  : eq
            
c[cmp]# Complication comparisons start with 'b'
            c<#  : lt
            c<=# : lte
            c>#  : gt
            c>=# :gte
            c=#  : eq

b#|x#   For dice with natural complications
            2dSTx18 : STA Dice (2d20), 
                complication on 18 instead of 20
            2dDDx2  : D&D Dice (d20), 
                "crit fail" on 2 instead of 1
            2dDDb18  : D&D Dice (d20), 
                "critical" on 18 instead of 20
                
cb#|cx# Critical boon/complication (2x) when the dice rolls:
            > | >= : at least #
            < | <= ; at most #
            (A compare criteria must be specified)
```'''
    )

    embed_dict = {
        'title': 'Help',
        'type': 'rich',
        'description': cmd,
        'timestamp': str(datetime.now()),
    }

    embed = discord.Embed.from_dict(embed_dict)

    embed.add_field(name='Roll Modifiers', value=extra_rolls, inline=False)
    embed.add_field(name='Success and Failure', value=success, inline=False)
    embed.add_field(name='Boons and Complications', value=boon, inline=False)

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

    if message.content.startswith('/h'):
        embed_msg = create_help()
        await message.channel.send(None, embed=embed_msg)

    elif message.content.startswith('/r '):
        user_cmd = message.content[2:]
        if comment := comment_pattern.search(user_cmd):
            comment = '```\n#' + comment.group('comment') + '\n```'
        user_cmd = comment_pattern.sub('', user_cmd, count=1)
        results = roll_command(user_cmd)
        response = format_response(results)
        response.title = f'{message.author.display_name} : {user_cmd}'
        if comment:
            response.description = comment
        # response.set_thumbnail(url=message.author.avatar_url)
        response.set_author(
            name=message.author.display_name,
            icon_url=message.author.avatar_url,
        )
        await message.channel.send(None, embed=response)

    elif message.content.startswith('/rf '):
        user_cmd = message.content[3:]
        if comment := comment_pattern.search(user_cmd):
            comment = '```\n#' + comment.group('comment') + '\n```'
        user_cmd = comment_pattern.sub('', user_cmd, count=1)
        results = roll_command(user_cmd)
        response = format_response_full(results)
        response.title = f'{message.author.display_name} : {user_cmd}'
        if comment:
            response.description = comment
        # response.set_thumbnail(url=message.author.avatar_url)
        response.set_author(
            name=message.author.display_name,
            icon_url=message.author.avatar_url,
        )
        await message.channel.send(None, embed=response)

    elif message.content.startswith('/dice'):
        dice_name = None
        dice_data = {key: (val['dice_name'] if 'dice_name' in val else "N/A") for key, val in _dice_types.items()}
        if len(message.content) > 5:
            dice_name = message.content[5:].strip().upper()
            dice_data = _dice_types[dice_name]

        msg = pformat(dice_data, indent=2, width=120)
        msg = '```\n' + msg + '\n```'
        await message.channel.send(msg)


if __name__ == '__main__':
    with open('env.json', 'r') as env_file:
        env = json.load(env_file)

    with open('dice.json', 'r') as dice_file:
        _dice_types = json.load(dice_file)

    supported_dice = (
        r'|'.join(map(str, sorted(_dice_types, key=len, reverse=True)))
    )

    supported_lc_dice = (
        r'|'.join(map(lambda x: x.lower(), sorted(_dice_types, key=len, reverse=True)))
    )

    base_roll_string = re.compile(
        r'(?P<num_dice>\d+)[dD](?P<dice_type>\d+|'
        + supported_dice
        + r'|'
        + supported_lc_dice
        + r')(?P<options>.*)'
    )

    discord_token = env['TOKEN']
    random.seed()
    client.run(discord_token)
