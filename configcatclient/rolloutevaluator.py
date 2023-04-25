import hashlib
import semver

from .logger import Logger

from .constants import TARGETING_RULES, VALUE, VARIATION_ID, COMPARISON_ATTRIBUTE, \
    COMPARATOR, PERCENTAGE, SETTING_TYPE, SERVED_VALUE, CONDITIONS, PERCENTAGE_OPTIONS, PERCENTAGE_RULE_ATTRIBUTE, \
    COMPARISON_RULE, STRING_LIST_VALUE, DOUBLE_VALUE, STRING_VALUE, FEATURE_FLAGS, PREFERENCES, SALT, SEGMENTS, \
    SEGMENT_CONDITION, DEPENDENT_FLAG_CONDITION, SEGMENT_INDEX, SEGMENT_COMPARATOR, SEGMENT_RULES, SEGMENT_NAME, \
    DEPENDENCY_SETTING_KEY, DEPENDENCY_COMPARATOR
from .user import User


class RolloutEvaluator(object):
    SEMANTIC_VERSION_COMPARATORS = ['<', '<=', '>', '>=']
    COMPARATOR_TEXTS = [
        'IS ONE OF',
        'IS NOT ONE OF',
        'CONTAINS',
        'DOES NOT CONTAIN',
        'IS ONE OF (SemVer)',
        'IS NOT ONE OF (SemVer)',
        '< (SemVer)',
        '<= (SemVer)',
        '> (SemVer)',
        '>= (SemVer)',
        '= (Number)',
        '<> (Number)',
        '< (Number)',
        '<= (Number)',
        '> (Number)',
        '>= (Number)',
        'IS ONE OF (Sensitive)',
        'IS NOT ONE OF (Sensitive)'
        
        'BEFORE (DateTime)',
        'AFTER (DateTime)',
        'EQUALS (Sensitive)',
        'DOSE NOT EQUAL (Sensitive)',
        'STARTS WITH (Sensitive)',
        'ENDS WITH (Sensitive)',
        'ARRAY CONTAINS (Sensitive)',
        'ARRAY DOES NOT CONTAIN (Sensitive)'
    ]
    COMPARISON_VALUES = [
        STRING_LIST_VALUE,  # IS ONE OF
        STRING_LIST_VALUE,  # IS NOT ONE OF
        STRING_VALUE,       # CONTAINS
        STRING_VALUE,       # DOES NOT CONTAIN
        STRING_LIST_VALUE,  # IS ONE OF (SemVer)
        STRING_LIST_VALUE,  # IS NOT ONE OF (SemVer)
        STRING_VALUE,       # < (SemVer)
        STRING_VALUE,       # <= (SemVer)
        STRING_VALUE,       # > (SemVer)
        STRING_VALUE,       # >= (SemVer)
        DOUBLE_VALUE,       # = (Number)
        DOUBLE_VALUE,       # <> (Number)
        DOUBLE_VALUE,       # < (Number)
        DOUBLE_VALUE,       # <= (Number)
        DOUBLE_VALUE,       # > (Number)
        DOUBLE_VALUE,       # >= (Number)
        STRING_LIST_VALUE,  # IS ONE OF (Sensitive)
        STRING_LIST_VALUE,  # IS NOT ONE OF (Sensitive)

        STRING_VALUE,       # BEFORE (DateTime)
        STRING_VALUE,       # AFTER (DateTime)
        STRING_VALUE,       # EQUALS (Sensitive)
        STRING_VALUE,       # DOSE NOT EQUAL (Sensitive)
        STRING_VALUE,       # STARTS WITH (Sensitive)
        STRING_VALUE,       # ENDS WITH (Sensitive)
        STRING_VALUE,       # ARRAY CONTAINS (Sensitive)
        STRING_VALUE        # ARRAY DOES NOT CONTAIN (Sensitive)
    ]
    SETTING_TYPES = ['b', 's', 'i', 'd']
    SEGMENT_COMPARATOR_TEXTS = ['IS IN SEGMENT', 'IS NOT IN SEGMENT']
    DEPENDENCY_COMPARATOR_TEXTS = ['EQUALS', 'DOES NOT EQUAL']

    def __init__(self, log):
        self.log = log

    def evaluate(self, key, user, default_value, default_variation_id, config):  # noqa: C901
        """
        returns value, variation_id, matched_evaluation_rule, matched_evaluation_percentage_rule, error, setting_type
        """
        settings = config.get(FEATURE_FLAGS, {})
        salt = config.get(PREFERENCES, {}).get(SALT, '')
        setting_descriptor = settings.get(key)

        if setting_descriptor is None:
            error = 'Failed to evaluate setting \'%s\' (the key was not found in config JSON). ' \
                    'Returning the `%s` parameter that you specified in your application: \'%s\'. ' \
                    'Available keys: [%s].'
            error_args = (key, 'default_value', str(default_value), ', '.join("'{}'".format(s) for s in list(settings)))
            self.log.error(error, *error_args, event_id=1001)
            return default_value, default_variation_id, None, None, Logger.format(error, error_args), None

        targeting_rules = setting_descriptor.get(TARGETING_RULES, [])
        setting_type = setting_descriptor.get(SETTING_TYPE)
        percentage_rule_attribute = setting_descriptor.get(PERCENTAGE_RULE_ATTRIBUTE)

        user_has_invalid_type = user is not None and type(user) is not User
        if user_has_invalid_type:
            self.log.warning('Cannot evaluate targeting rules and %% options for setting \'%s\' '
                             '(User Object is not an instance of User type).',
                             key, event_id=4001)
            user = None

        if user is None:
            if not user_has_invalid_type and len(targeting_rules) > 0:
                self.log.warning('Cannot evaluate targeting rules and %% options for setting \'%s\' '
                                 '(User Object is missing). '
                                 'You should pass a User Object to the evaluation methods like `get_value()` '
                                 'in order to make targeting work properly. '
                                 'Read more: https://configcat.com/docs/advanced/user-object/',
                                 key, event_id=3001)
            return_value = self._get_value(setting_descriptor, setting_type, default_value)
            return_variation_id = setting_descriptor.get(VARIATION_ID, default_variation_id)
            self.log.info('%s', 'Returning [%s]' % str(return_value), event_id=5000)
            return return_value, return_variation_id, None, None, None, setting_type

        log_entries = ['Evaluating get_value(\'%s\').' % key, 'User object:\n%s' % str(user)]

        try:
            # Evaluate targeting rules
            for targeting_rule in targeting_rules:
                conditions = targeting_rule.get(CONDITIONS, [])
                served_value = targeting_rule.get(SERVED_VALUE)
                percentage_options = targeting_rule.get(PERCENTAGE_OPTIONS, [])

                value = self._get_value(served_value, setting_type, default_value)
                variation_id = served_value.get(VARIATION_ID, default_variation_id)

                if self.evaluate_conditions(conditions, user, key, salt, value, config, log_entries):
                    return value, variation_id, targeting_rule, None, None, setting_type

                # Evaluate variations
                if len(percentage_options) > 0:
                    user_key = user.get_attribute(percentage_rule_attribute) if percentage_rule_attribute is not None else user.get_identifier()
                    hash_candidate = ('%s%s' % (key, user_key)).encode('utf-8')
                    hash_val = int(hashlib.sha1(hash_candidate).hexdigest()[:7], 16) % 100

                    bucket = 0
                    for percentage_option in percentage_options or []:
                        bucket += percentage_option.get(PERCENTAGE, 0)
                        if hash_val < bucket:
                            percentage_value = self._get_value(percentage_option, setting_type, default_value)
                            variation_id = percentage_option.get(VARIATION_ID, default_variation_id)
                            log_entries.append('Evaluating %% options. Returning %s' % percentage_value)
                            return percentage_value, variation_id, None, percentage_option, None, setting_type

            return_value = self._get_value(setting_descriptor, setting_type, default_value)
            return_variation_id = setting_descriptor.get(VARIATION_ID, default_variation_id)
            log_entries.append('Returning %s' % return_value)
            return return_value, return_variation_id, None, None, None, setting_type
        finally:
            self.log.info('%s', '\n'.join(log_entries), event_id=5000)

    def _format_match_rule(self, comparison_attribute, user_value, comparator, comparison_value, value):
        return 'Evaluating rule: [%s:%s] [%s] [%s] => match, returning: %s' \
               % (comparison_attribute, user_value, self.COMPARATOR_TEXTS[comparator], comparison_value, value)

    def _format_no_match_rule(self, comparison_attribute, user_value, comparator, comparison_value):
        return 'Evaluating rule: [%s:%s] [%s] [%s] => no match' \
               % (comparison_attribute, user_value, self.COMPARATOR_TEXTS[comparator], comparison_value)

    def _format_validation_error_rule(self, comparison_attribute, user_value, comparator, comparison_value, error):
        return 'Evaluating rule: [%s:%s] [%s] [%s] => SKIP rule. Validation error: %s' \
               % (comparison_attribute, user_value, self.COMPARATOR_TEXTS[comparator], comparison_value, error)

    def evaluate_conditions(self, conditions, user, key, salt, value, config, log_entries):
        segments = config.get(SEGMENTS, [])

        # TODO: sort conditions by order: comparison_rule > segment_condition > dependent_flag_condition
        for condition in conditions:
            comparison_rule = condition.get(COMPARISON_RULE)
            segment_condition = condition.get(SEGMENT_CONDITION)
            dependent_flag_condition = condition.get(DEPENDENT_FLAG_CONDITION)

            if comparison_rule is not None:
                if not self._evaluate_comparison_rule_condition(comparison_rule, user, key, salt, value, log_entries):
                    return False
            elif segment_condition is not None:
                if not self._evaluate_segment_condition(segment_condition, user, salt, value, segments, log_entries):
                    return False
            elif dependent_flag_condition is not None:
                if not self._evaluate_dependent_flag_condition(dependent_flag_condition, user, config, key, salt, value, log_entries):
                    return False

        return True

    def _evaluate_dependent_flag_condition(self, dependent_flag_condition, user, config, key, salt, value, log_entries):
        # TODO: tree circle checking
        dependency_key = dependent_flag_condition.get(DEPENDENCY_SETTING_KEY)
        dependency_comparator = dependent_flag_condition.get(DEPENDENCY_COMPARATOR)

        # TODO: put the logs of the dependency flag evaluation into the log_entries
        dependency_value, dependency_variation_id, _, _, error, setting_type = self.evaluate(dependency_key, user, None, None, config)
        if error is not None:
            log_entries.append('Evaluating dependent flag condition. Dependency error: %s' % error)
            return False

        dependency_comparison_value = self._get_value(dependent_flag_condition, setting_type, None)
        if dependency_comparison_value is None:
            log_entries.append('Evaluating dependent flag condition. Dependency comparison value is None.')
            return False

        # TODO: evaluation log entries
        # EQUALS
        if dependency_comparator == 0:
            return dependency_value == dependency_comparison_value
        # DOES NOT EQUAL
        elif dependency_comparator == 1:
            return dependency_value != dependency_comparison_value

        return False

    def _evaluate_segment_condition(self, segment_condition, user, salt, value, segments, log_entries):
        segment_index = segment_condition.get(SEGMENT_INDEX)
        segment = segments[segment_index]
        segment_name = segment.get(SEGMENT_NAME, '')
        segment_comparator = segment_condition.get(SEGMENT_COMPARATOR)
        segment_comparison_rules = segment.get(SEGMENT_RULES, [])

        # TODO: evaluation log entries
        # IS IN SEGMENT
        if segment_comparator == 0:
            for segment_comparison_rule in segment_comparison_rules:
                if not self._evaluate_comparison_rule_condition(segment_comparison_rule, user, segment_name, salt, value, log_entries):
                    return False
            return True
        # IS NOT IN SEGMENT
        elif segment_comparator == 1:
            for segment_comparison_rule in segment_comparison_rules:
                if self._evaluate_comparison_rule_condition(segment_comparison_rule, user, segment_name, salt, value, log_entries):
                    return True
            return False

    def _evaluate_comparison_rule_condition(self, comparison_rule, user, context_salt, salt, value, log_entries):
        comparison_attribute = comparison_rule.get(COMPARISON_ATTRIBUTE)
        comparator = comparison_rule.get(COMPARATOR)
        comparison_value = comparison_rule.get(self.COMPARISON_VALUES[comparator])

        user_value = user.get_attribute(comparison_attribute)
        if user_value is None or not user_value:
            log_entries.append(
                self._format_no_match_rule(comparison_attribute, user_value, comparator, comparison_value)
            )
            return False

        # IS ONE OF
        if comparator == 0:
            if str(user_value) in [x.strip() for x in comparison_value]:
                log_entries.append(self._format_match_rule(comparison_attribute, user_value, comparator,
                                                           comparison_value, value))
                return True
        # IS NOT ONE OF
        elif comparator == 1:
            if str(user_value) not in [x.strip() for x in comparison_value]:
                log_entries.append(self._format_match_rule(comparison_attribute, user_value, comparator,
                                                           comparison_value, value))
                return True
        # CONTAINS
        elif comparator == 2:
            if str(user_value).__contains__(str(comparison_value)):
                log_entries.append(self._format_match_rule(comparison_attribute, user_value, comparator,
                                                           comparison_value, value))
                return True
        # DOES NOT CONTAIN
        elif comparator == 3:
            if not str(user_value).__contains__(str(comparison_value)):
                log_entries.append(self._format_match_rule(comparison_attribute, user_value, comparator,
                                                           comparison_value, value))
                return True
        # IS ONE OF, IS NOT ONE OF (Semantic version)
        elif 4 <= comparator <= 5:
            try:
                match = False
                for x in filter(None, [x.strip() for x in comparison_value]):
                    match = semver.VersionInfo.parse(str(user_value).strip()).match('==' + x) or match
                if (match and comparator == 4) or (not match and comparator == 5):
                    log_entries.append(self._format_match_rule(comparison_attribute, user_value, comparator,
                                                               comparison_value, value))
                    return True
            except ValueError as e:
                message = self._format_validation_error_rule(comparison_attribute, user_value, comparator,
                                                             comparison_value, str(e))
                self.log.warning(message)
                log_entries.append(message)
                return False
        # LESS THAN, LESS THAN OR EQUALS TO, GREATER THAN, GREATER THAN OR EQUALS TO (Semantic version)
        elif 6 <= comparator <= 9:
            try:
                if semver.VersionInfo.parse(str(user_value).strip()).match(
                        self.SEMANTIC_VERSION_COMPARATORS[comparator - 6] + str(comparison_value).strip()
                ):
                    log_entries.append(self._format_match_rule(comparison_attribute, user_value, comparator,
                                                               comparison_value, value))
                    return True
            except ValueError as e:
                message = self._format_validation_error_rule(comparison_attribute, user_value, comparator,
                                                             comparison_value, str(e))
                self.log.warning(message)
                log_entries.append(message)
                return False
        # =, <>, <, <=, >, >= (Number)
        elif 10 <= comparator <= 15:
            try:
                user_value_float = float(str(user_value).replace(",", "."))
                comparison_value_float = float(str(comparison_value).replace(",", "."))

                if (comparator == 10 and user_value_float == comparison_value_float) \
                        or (comparator == 11 and user_value_float != comparison_value_float) \
                        or (comparator == 12 and user_value_float < comparison_value_float) \
                        or (comparator == 13 and user_value_float <= comparison_value_float) \
                        or (comparator == 14 and user_value_float > comparison_value_float) \
                        or (comparator == 15 and user_value_float >= comparison_value_float):
                    log_entries.append(self._format_match_rule(comparison_attribute, user_value, comparator,
                                                               comparison_value, value))
                    return True
            except Exception as e:
                message = self._format_validation_error_rule(comparison_attribute, user_value, comparator,
                                                             comparison_value, str(e))
                self.log.warning(message)
                log_entries.append(message)
                return False
        # IS ONE OF (Sensitive)
        elif comparator == 16:
            if str(hashlib.sha256(
                    user_value.encode('utf8') + salt.encode('utf8') + context_salt.encode('utf8')).hexdigest()) in [
                x.strip() for x in comparison_value
            ]:
                log_entries.append(self._format_match_rule(comparison_attribute, user_value, comparator,
                                                           comparison_value, value))
                return True
        # IS NOT ONE OF (Sensitive)
        elif comparator == 17:
            if str(hashlib.sha256(
                    user_value.encode('utf8') + salt.encode('utf8') + context_salt.encode('utf8')).hexdigest()) not in [
                x.strip() for x in comparison_value
            ]:
                log_entries.append(self._format_match_rule(comparison_attribute, user_value, comparator,
                                                           comparison_value, value))
                return True

        # STARTS WITH (Sensitive)
        elif comparator == 22:
            underscore_index = comparison_value.index('_')
            length = int(comparison_value[:underscore_index])
            if len(user_value) >= length and \
                    str(hashlib.sha256(user_value[:length].encode('utf8') + salt.encode('utf8') + context_salt.encode(
                        'utf8')).hexdigest()) == comparison_value[underscore_index + 1:]:
                log_entries.append(self._format_match_rule(comparison_attribute, user_value, comparator,
                                                           comparison_value, value))
                return True

        # ENDS WITH (Sensitive)
        elif comparator == 23:
            underscore_index = comparison_value.index('_')
            length = int(comparison_value[:underscore_index])
            if len(user_value) >= length and \
                    str(hashlib.sha256(user_value[-length:].encode('utf8') + salt.encode('utf8') + context_salt.encode(
                        'utf8')).hexdigest()) == comparison_value[underscore_index + 1:]:
                log_entries.append(self._format_match_rule(comparison_attribute, user_value, comparator,
                                                           comparison_value, value))
                return True

        log_entries.append(self._format_no_match_rule(comparison_attribute, user_value, comparator, comparison_value))
        return False

    def _get_value(self, dictionary, setting_type, default_value):
        # TODO: type checking
        value = dictionary.get(VALUE)
        return value.get(self.SETTING_TYPES[setting_type], default_value) if value is not None else default_value
