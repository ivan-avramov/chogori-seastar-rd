#!/usr/bin/env python3
#
# This file is open source software, licensed to you under the terms
# of the Apache License, Version 2.0 (the "License").  See the NOTICE file
# distributed with this work for additional information regarding copyright
# ownership.  You may not use this file except in compliance with the License.
#
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
#
#
# Copyright (C) 2024 Scylladb, Ltd.
#

import argparse
import math
import json
import re
import subprocess
import sys
import time
import unittest
import urllib.request
import urllib.parse
import yaml

from typing import Optional
from collections import namedtuple


class Exposition:
    @classmethod
    def from_hist(cls,
                  name: str,
                  hist: list[tuple[float, int]],
                  sum_: int,
                  count: int) -> 'Exposition':
        # ignore these values, we might need to verify them in future
        _, _ = sum_, count
        buckets = (cls.value_to_bucket(le - 1) for le, _ in hist)
        deltas = []
        last_n = 0
        for _, n in hist:
            delta = n - last_n
            last_n = n
            deltas.append(delta)
        return cls(name, dict(zip(buckets, deltas)), {})

    @staticmethod
    def value_to_bucket(value):
        low = 2 ** math.floor(math.log(value, 2))
        high = 2 * low
        dif = (high - low) / 4
        return low + dif * math.floor((value - low) / dif)

    @staticmethod
    def _values_to_histogram(values):
        hist = {}
        for val in values:
            bucket = Exposition.value_to_bucket(val)
            if bucket in hist:
                hist[bucket] += 1
            else:
                hist[bucket] = 1
        return hist

    @classmethod
    def from_conf(cls,
                  name: str,
                  type_: str,
                  values: list[str],
                  labels: dict[str, str]) -> 'Exposition':
        if type_ in ('gauge', 'counter'):
            assert len(values) == 1
            return cls(name, float(values[0]), labels)
        if type_ == 'histogram':
            hist = cls._values_to_histogram(float(v) for v in values)
            return cls(name, hist, {})
        raise NotImplementedError(f'unsupported type: {type_}')

    def __init__(self,
                 name: str,
                 value: int | list[tuple[float, int]],
                 labels: dict[str, str]) -> None:
        self.name = name
        self.value = value
        self.labels = labels

    def __repr__(self):
        return f"{self.name=}, {self.value=}, {self.labels=}"

    def __eq__(self, other):
        if not isinstance(other, Exposition):
            return False
        return self.value == other.value


class Metrics:
    prefix = 'seastar'
    group = 'test_group'
    # parse lines like:
    # rest_api_scheduler_queue_length{group="main",shard="0"} 0.000000
    # where:
    #   - "rest_api" is the prometheus prefix
    #   - "scheduler" is the metric group name
    #   - "queue_length" is the name of the metric
    #   - the kv pairs in "{}" are labels"
    #   - "0.000000" is the value of the metric
    # this format is compatible with
    # https://github.com/prometheus/docs/blob/main/content/docs/instrumenting/exposition_formats.md
    # NOTE: scylla does not include timestamp in the exported metrics
    pattern = re.compile(r'''(?P<metric_name>\w+)   # rest_api_scheduler_queue_length
                             \{(?P<labels>[^\}]*)\} # {group="main",shard="0"}
                             \s+                    # <space>
                             (?P<value>[^\s]+)      # 0.000000''', re.X)

    def __init__(self, lines: list[str]) -> None:
        self.lines: list[str] = lines

    @classmethod
    def full_name(cls, name: str) -> str:
        '''return the full name of a metrics
        '''
        return f'{cls.group}_{name}'

    @staticmethod
    def _parse_labels(s: str) -> dict[str, str]:
        return dict(name_value.split('=', 1) for name_value in s.split(','))

    def get(self,
            name: Optional[str] = None,
            labels: Optional[dict[str, str]] = None) -> list[Exposition]:
        '''Return all expositions matching the given name and labels
        '''
        full_name = None
        if name is not None:
            full_name = f'{self.prefix}_{self.group}_{name}'
        metric_type = None

        # for histogram and summary as they are represented with multiple lines
        hist_name = ''
        hist_buckets = []
        hist_sum = 0
        hist_count = 0

        for line in self.lines:
            if not line:
                continue
            if line.startswith('# HELP'):
                continue
            if line.startswith('# TYPE'):
                _, _, type_metric_name, metric_type = line.split()
                if hist_buckets:
                    yield Exposition.from_hist(hist_name,
                                               hist_buckets,
                                               hist_sum,
                                               hist_count)
                    hist_buckets = []
                if metric_type in ('histogram', 'summary'):
                    hist_name = type_metric_name
                continue
            matched = self.pattern.match(line)
            assert matched, f'malformed metric line: {line}'

            value_metric_name = matched.group('metric_name')
            if full_name and not value_metric_name.startswith(full_name):
                continue

            metric_labels = self._parse_labels(matched.group('labels'))
            if labels is not None and metric_labels != labels:
                continue

            metric_value = float(matched.group('value'))
            if metric_type == 'histogram':
                if value_metric_name == f'{type_metric_name}_bucket':
                    last_value = 0
                    if hist_buckets:
                        last_value = hist_buckets[-1][1]
                    if metric_value - last_value != 0:
                        le = metric_labels['le'].strip('"')
                        hist_buckets.append((float(le), metric_value))
                elif value_metric_name == f'{type_metric_name}_sum':
                    hist_sum = metric_value
                elif value_metric_name == f'{type_metric_name}_count':
                    hist_count = metric_value
                else:
                    raise RuntimeError(f'unknown histogram value: {line}')
            elif metric_type == 'summary':
                raise NotImplementedError('unsupported type: summary')
            else:
                yield Exposition(type_metric_name,
                                 metric_value,
                                 metric_labels)
        if hist_buckets:
            yield Exposition.from_hist(hist_name,
                                       hist_buckets,
                                       hist_sum,
                                       hist_count)

    def get_help(self, name: str) -> Optional[str]:
        full_name = f'{self.prefix}_{self.group}_{name}'
        header = f'# HELP {full_name}'
        for line in self.lines:
            if line.startswith(header):
                tokens = line.split(maxsplit=3)
                return tokens[-1]
        return None


class TestPrometheus(unittest.TestCase):
    exporter_path = None
    exporter_process = None
    exporter_config = None
    port = 10001
    prometheus = None
    prometheus_scrape_interval = 15

    @classmethod
    def setUpClass(cls) -> None:
        args = [cls.exporter_path,
                '--port', f'{cls.port}',
                '--conf', cls.exporter_config,
                '--smp=2']
        cls.exporter_process = subprocess.Popen(args,
                                                stdout=subprocess.PIPE,
                                                stderr=subprocess.DEVNULL,
                                                bufsize=0, text=True)
        # wait until the server is ready for serve
        cls.exporter_process.stdout.readline()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.exporter_process.terminate()

    @classmethod
    def _get_metrics(cls,
                     name: Optional[str] = None,
                     labels: Optional[dict[str, str]] = None,
                     with_help: bool = True,
                     aggregate: bool = True) -> Metrics:
        query: dict[str, str] = {}
        if name is not None:
            query['__name__'] = name
        if labels is not None:
            query.update(labels)
        if not with_help:
            query['__help__'] = 'false'
        if not aggregate:
            query['__aggregate__'] = 'false'
        params = urllib.parse.urlencode(query)
        host = 'localhost'
        url = f'http://{host}:{cls.port}/metrics?{params}'
        with urllib.request.urlopen(url) as f:
            body = f.read().decode('utf-8')
            return Metrics(body.rstrip().split('\n'))

    def test_filtering_by_label_sans_aggregation(self) -> None:
        labels = {'private': '1'}
        metrics = self._get_metrics(labels=labels)
        actual_values = list(metrics.get())
        expected_values = []
        with open(self.exporter_config, encoding='utf-8') as f:
            config = yaml.safe_load(f)
        for metric in config['metrics']:
            name = metric['name']
            metric_name = f'{Metrics.prefix}_{Metrics.group}_{name}'
            metric_labels = metric['labels']
            if metric_labels != labels:
                continue
            e = Exposition.from_conf(metric_name,
                                     metric['type'],
                                     metric['values'],
                                     metric_labels)
            expected_values.append(e)
        self.assertCountEqual(actual_values, expected_values)

    def test_filtering_by_label_with_aggregation(self) -> None:
        TestCase = namedtuple('TestCase', ['label', 'regex', 'found'])
        label = 'private'
        tests = [
            TestCase(label=label, regex='dne', found=0),
            TestCase(label=label, regex='404', found=0),
            TestCase(label=label, regex='2', found=1),
            # aggregated
            TestCase(label=label, regex='2|3', found=1),
        ]
        for test in tests:
            with self.subTest(regex=test.regex, found=test.found):
                metrics = self._get_metrics(labels={test.label: test.regex})
                values = list(metrics.get())
                self.assertEqual(len(values), test.found)

    def test_aggregated(self) -> None:
        name = 'counter_1'
        # see also rest_api_httpd.cc::aggregate_by_name
        TestCase = namedtuple('TestCase', ['aggregate', 'expected_values'])
        tests = [
            TestCase(aggregate=False, expected_values=[1, 2]),
            TestCase(aggregate=True, expected_values=[3])
        ]
        for test in tests:
            with self.subTest(aggregate=test.aggregate,
                              values=test.expected_values):
                metrics = self._get_metrics(Metrics.full_name(name), aggregate=test.aggregate)
                expositions = metrics.get(name)
                actual_values = sorted(e.value for e in expositions)
                self.assertEqual(actual_values, test.expected_values)

    def test_help(self) -> None:
        name = 'counter_1'
        tests = [True, False]
        for with_help in tests:
            with self.subTest(with_help=with_help):
                metrics = self._get_metrics(Metrics.full_name(name), with_help=with_help)
                msg = metrics.get_help(name)
                if with_help:
                    self.assertIsNotNone(msg)
                else:
                    self.assertIsNone(msg)

    @staticmethod
    def _from_native_histogram(values) -> dict[float, float]:
        results = {}
        for v in values:
            bucket = Exposition.value_to_bucket(float(v[2]) - 1)
            results[bucket] = float(v[3])
        return results

    @staticmethod
    def _query_prometheus(host: str, query: str, type_: str) -> float | dict[float, float]:
        url = f'http://{host}/api/v1/query?query={query}'
        headers = {"Accept": "application/json"}
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req) as f:
            results = json.load(f)["data"]["result"][0]
            if type_ == 'histogram':
                buckets = results["histogram"][1]["buckets"]
                return TestPrometheus._from_native_histogram(buckets)
            return float(results["value"][1])

    def test_protobuf(self) -> None:
        if self.prometheus is None:
            self.skipTest("prometheus is not configured")

        # Prometheus does not allow us to push metrics to it, neither
        # can we force it to scrape an exporter, so we have to wait
        # until prometheus scrapes the server
        time.sleep(self.prometheus_scrape_interval + 1)
        with open(self.exporter_config, encoding='utf-8') as f:
            config = yaml.safe_load(f)

        labels = {'private': '1'}
        for metric in config['metrics']:
            name = metric['name']
            metric_name = f'{Metrics.prefix}_{Metrics.group}_{name}'
            metric_labels = metric['labels']
            if metric_labels != labels:
                continue
            metric_type = metric['type']
            metric_value = metric['values']
            e = Exposition.from_conf(metric_name,
                                     metric_type,
                                     metric_value,
                                     metric_labels)
            res = self._query_prometheus(self.prometheus,
                                         metric_name,
                                         metric_type)
            self.assertEqual(res, e.value)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--exporter',
                        required=True,
                        help='Path to the exporter executable')
    parser.add_argument('--config',
                        required=True,
                        help='Path to the metrics definition file')
    parser.add_argument('--prometheus',
                        help='A Prometheus to connect to')
    parser.add_argument('--prometheus-scrape-interval',
                        type=int,
                        help='Prometheus scrape interval (in seconds)',
                        default=15)
    opts, remaining = parser.parse_known_args()
    remaining.insert(0, sys.argv[0])
    TestPrometheus.exporter_path = opts.exporter
    TestPrometheus.exporter_config = opts.config
    TestPrometheus.prometheus = opts.prometheus
    TestPrometheus.prometheus_scrape_interval = opts.prometheus_scrape_interval
    unittest.main(argv=remaining)
