import yaml
import re
import os
import subprocess
import urllib.request
import tempfile
import argparse
import sys

SSML_CWD = ".humvee"
HUMVEE_URL = "https://repo.splunk.com/artifactory/maven-splunk-local/com/splunk/humvee-scala_2.11/1.2.1-SNAPSHOT/humvee-scala_2.11-1.2.1-20201022.220521-1.jar"


def main(args):
    parser = argparse.ArgumentParser()
    parser.add_argument('--skip-errors', action='store_true', default=False)
    parser.add_argument('--debug', action='store_true', default=False)
    parser.add_argument('test_files', type=str, nargs='+', help="test files to be checked")
    parsed = parser.parse_args(args)
    build_humvee()
    status = True
    passed_tests = []
    failed_tests = []
    for t in parsed.test_files:
        cur_status = test_detection(t, parsed)
        status = status & cur_status
        if cur_status:
            passed_tests.append(t)
        else:
            failed_tests.append(t)
        if not status and not parsed.skip_errors:
            _exit(1, passed_tests, failed_tests)
    if status:
        _exit(0, passed_tests, failed_tests)
    else:
        _exit(1, passed_tests, failed_tests)


def _exit(code, passed, failed):
    print("\nPassed tests")
    print("=================")
    print("\n".join(passed))
    print("\nFailed tests")
    print("=================")
    print("\n".join(failed))
    exit(code)


def get_path(p):
    return os.path.join(os.path.join(os.path.dirname(__file__), p))


def get_pipeline_input(data):
    return '| from read_text("%s") ' \
           '| select from_json_object(value) as input_event ' \
           '| eval timestamp=parse_long(ucast(map_get(input_event, "_time"), "string", null))' % data


def get_pipeline_output(pass_condition):
    return '%s;' % pass_condition


def extract_pipeline(search, data, pass_condition):
    updated_search = re.sub(r"\|\s*from\s+read_ssa_enriched_events\(\s*\)",
                            get_pipeline_input(data),
                            search)
    updated_search = re.sub(r"\|\s*into\s+write_ssa_detected_events\(\s*\)\s*;",
                            get_pipeline_output(pass_condition),
                            updated_search)
    return updated_search


def build_humvee():
    if not os.path.exists(get_path(SSML_CWD)):
        os.mkdir(get_path(SSML_CWD))
    if not os.path.exists(get_path("%s/humvee.jar" % SSML_CWD)):
        urllib.request.urlretrieve(HUMVEE_URL, "%s/humvee.jar" % get_path(SSML_CWD))


def activate_detection(detection, data, pass_condition):
    with open(detection, 'r') as fh:
        parsed_detection = yaml.safe_load(fh)
        # Returns pipeline only for SSA detections
        if parsed_detection['type'] == "SSA":
            pipeline = extract_pipeline(parsed_detection['search'], data, pass_condition)
            return pipeline
        else:
            return None


def test_detection(test, args):
    with open(test, 'r') as fh:
        test_desc = yaml.safe_load(fh)
        name = test_desc['name']
        print("Testing %s" % name)
        # Download data to temporal folder
        data_dir = tempfile.TemporaryDirectory(prefix="data", dir=get_path("%s" % SSML_CWD))
        # Temporal solution
        if test_desc['attack_data'] is None or len(test_desc['attack_data']) == 0:
            print("No dataset in testing file")
            return False
        d = test_desc['attack_data'][0]
        test_data = os.path.abspath("%s/%s" % (data_dir.name, d['file_name']))
        if args.debug:
            print("Downloading dataset %s from %s" % (d['file_name'], d['data']))
        urllib.request.urlretrieve(d['data'], test_data)
        # for d in test_desc['attack_data']:
        #     test_data = "%s/%s" % (data_dir.name, d['file_name'])
        #     urllib.request.urlretrieve(d['data'], test_data)
        for detection in test_desc['detections']:
            detection_file = get_path("../detections/%s" % detection['file'])
            spl2 = activate_detection(detection_file, test_data, detection['pass_condition'])
            if args.debug:
                print("\n Running test with this pipeline")
                print(spl2)
                print("\nWill test the pipeline with this data")
                with open(test_data, 'r') as test_data_fh:
                    for line in test_data_fh.readlines()[:10]:
                        print(line.strip())
            if spl2 is not None:
                spl2_file = os.path.join(data_dir.name, "test.spl2")
                test_out = "%s.out" % spl2_file
                test_status = "%s.status" % test_out
                with open(spl2_file, 'w') as spl2_fh:
                    spl2_fh.write(spl2)
                # Execute SPL2
                subprocess.run(["/usr/bin/java",
                                "-jar", get_path("%s/humvee.jar" % SSML_CWD),
                                'cli',
                                '-i', spl2_file,
                                '-o', test_out],
                               stderr=subprocess.DEVNULL)
                # Validate that it can run
                with open(test_status, "r") as test_status_fh:
                    status = '\n'.join(test_status_fh.readlines())
                    if status == "OK\n":
                        print("Pipeline was executed")
                    else:
                        print("Pipeline can not be executed")
                        print("-------------------")
                        print(status)
                        print("\nTested query from %s:\n" % detection['file'])
                        print(spl2)
                        return False
                # Validate the results
                with open(test_out, 'r') as test_out_fh:
                    res = test_out_fh.readlines()
                    if args.debug:
                        print("\nThis is what came out of the pipeline\n")
                        print("\n".join(res[:10]))
                    if len(res) > 0:
                        print("Output expected")
                    else:
                        print("Pass condition %s didn't produce any events" % detection['pass_condition'])
                        return False
    return True


if __name__ == '__main__':
    main(sys.argv[1:])