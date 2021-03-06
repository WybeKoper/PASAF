import time
import os
import requests
import json
import math
import traceback
from kubernetes import client, config
from datetime import datetime
from prometheus_client import start_http_server, Gauge

# start_http_server(8000)
# cooldown_metric = Gauge('dhalion_autoscaler_cooldown', 'amount of seconds remaining in cooldown')
# cooldown_metric.set(-1)
#
# backpressure_metric = Gauge('dhalion_autoscaler_backpressure', 'backpressure value')
# backpressure_metric.set(-1)
#
# consumer_lag_metric = Gauge('consumer_lag', 'consumer lag')
# consumer_lag_metric.set(-1)

# obtain environmental variables
cooldown = os.environ['COOLDOWN']
avg_over_time = os.environ['AVG_OVER_TIME']
min_replicas = int(os.environ['MIN_REPLICAS'])
max_replicas = int(os.environ['MAX_REPLICAS'])
sleep_time = int(os.environ['SLEEP_TIME'])
backpressure_lower_threshold = float(os.environ['BACKPRESSURE_LOWER_THRESHOLD'])
backpressure_upper_threshold = float(os.environ['BACKPRESSURE_UPPER_THRESHOLD'])
cpu_lower_threshold = float(os.environ['CPU_LOWER_THRESHOLD'])
cpu_upper_threshold = float(os.environ['CPU_UPPER_THRESHOLD'])
consumer_lag_threshold = float(os.environ['CONSUMER_LAG_THRESHOLD'])
scaling_factor = float(os.environ['SCALING_FACTOR_PERCENTAGE'])
lag_size = float(os.environ['LAG_SIZE'])
overprovisioning_factor = float(os.environ["OVERPROVISIONING_FACTOR"])
latency_threshold = float(os.environ["LATENCY_THRESHOLD"])

# cooldown = "120s"
# avg_over_time = "1m"
# min_replicas = 1
# max_replicas = 16
# sleep_time = 10
# backpressure_lower_threshold = 100
# backpressure_upper_threshold = 500
# cpu_lower_threshold = 0.6
# cpu_upper_threshold = 0.8
# consumer_lag_threshold = 10000
# scaling_factor = 0.2
# lag_size = 0.2
# overprovisioning_factor = 1.2
# latency_threshold = 5
#
# prometheus_server = "34.65.53.106:9090"
prometheus_server = "prometheus-server"
def run():
        while True:
                print('Executing Dhalion Script')

                input_rate = requests.get(
                        "http://" + prometheus_server + "/api/v1/query?query=sum(rate(kafka_server_brokertopicmetrics_messagesin_total{topic=\"\"}[" + avg_over_time +"]))")
                input_rate = input_rate.json()["data"]["result"][0]["value"][1]
                print("Input rate: " + str(input_rate))

                busy_time = requests.get(
                        "http://" + prometheus_server + "/api/v1/query?query=avg(avg_over_time(flink_taskmanager_job_task_busyTimeMsPerSecond[" + avg_over_time +"])) / 1000")
                busy_time = busy_time.json()["data"]["result"][0]["value"][1]
                print("Busy time: " + str(busy_time))

                deriv_consumer_lag = requests.get(
                        "http://" + prometheus_server + "/api/v1/query?query=avg(deriv(flink_taskmanager_job_task_operator_KafkaSourceReader_KafkaConsumer_records_lag_max[" + avg_over_time + "]))")
                deriv_consumer_lag = deriv_consumer_lag.json()["data"]["result"][0]["value"][1]
                print("Derivative consumer lag: " + str(deriv_consumer_lag))


                # obtain backpressure metric from prometheus
                backpressure_query = requests.get("http://" + prometheus_server + "/api/v1/query?query=max(avg_over_time(flink_taskmanager_job_task_backPressuredTimeMsPerSecond[" + avg_over_time +"]))")
                backpressure_value = backpressure_query.json()["data"]["result"][0]["value"][1]
                print("backpressure value: " + str(backpressure_value))
                # backpressure_metric.set(str(backpressure_value))

                # obtain throughput
                throughput_sink = requests.get("http://" + prometheus_server + "/api/v1/query?query=sum(flink_taskmanager_job_task_numRecordsInPerSecond{task_name=~\".*ink.*\"}) by (task_name)")
                throughput_sink_value = throughput_sink.json()["data"]["result"][0]["value"][1]
                print("throughput sink value: " + str(throughput_sink_value))

                # obtain consumer lag from prometheus
                consumer_lag = requests.get("http://" + prometheus_server + "/api/v1/query?query=avg(flink_taskmanager_job_task_operator_KafkaSourceReader_KafkaConsumer_records_lag_max)")
                consumer_lag = consumer_lag.json()["data"]["result"][0]["value"][1]
                print("consumer lag value: " + str(consumer_lag))
                # consumer_lag_metric.set(str(consumer_lag))

                # obtain cpu utilization from prometheus
                cpu_load = requests.get(
                        "http://" + prometheus_server + "/api/v1/query?query=avg(avg_over_time(flink_taskmanager_Status_JVM_CPU_Load[" + avg_over_time + "]))")
                cpu_load = cpu_load.json()["data"]["result"][0]["value"][1]
                print("cpu load value: " + str(cpu_load))

                latency = requests.get(
                        "http://" + prometheus_server + "/api/v1/query?query=avg(flink_taskmanager_job_task_operator_currentEmitEventTimeLag) / 1000")
                latency_value = latency.json()["data"]["result"][0]["value"][1]
                print("latency value: " + str(latency_value))

                # obtain cpu utilization from prometheus
                min_throughput = requests.get(
                        "http://" + prometheus_server + "/api/v1/query?query=min(sum(flink_taskmanager_job_task_numRecordsOutPerSecond{task_name!~\".*Sink.*\"}) by (task_name))")
                min_throughput = min_throughput.json()["data"]["result"][0]["value"][1]
                print("min throughput: " + str(min_throughput))

                # obtain previous scaling event
                previous_scaling_event = requests.get("http://" + prometheus_server + "/api/v1/query?query=deriv(flink_jobmanager_numRegisteredTaskManagers[" + cooldown + "])")
                previous_scaling_event = previous_scaling_event.json()["data"]["result"][0]["value"][1]
                print("taskmanager deriv: " + str(previous_scaling_event))

                # autenticate with kubernetes API
                config.load_incluster_config()
                v1 = client.AppsV1Api()

                # retrieving current number of taskmanagers from kubernetes API
                current_number_of_taskmanagers = None
                ret = v1.list_namespaced_deployment(watch=False, namespace="default", pretty=True, field_selector="metadata.name=flink-taskmanager")
                for i in ret.items:
                        current_number_of_taskmanagers = int(i.spec.replicas)
                print("current number of taskmanagers: " + str(current_number_of_taskmanagers))

                if float(previous_scaling_event) == 0:
                        if float(latency_value) > latency_threshold and float(deriv_consumer_lag) > 0:
                                increase_factor = float(input_rate) / float(throughput_sink_value)
                                print("increase_factor", increase_factor)
                                new_number_of_taskmanagers = math.ceil(current_number_of_taskmanagers*increase_factor * overprovisioning_factor)
                                if new_number_of_taskmanagers > max_replicas:
                                        new_number_of_taskmanagers = max_replicas
                                print("scaling up to: " + str(new_number_of_taskmanagers))

                        elif float(backpressure_value) < backpressure_lower_threshold and float(consumer_lag) < lag_size * float(input_rate) and current_number_of_taskmanagers > min_replicas and float(cpu_load) < cpu_lower_threshold:
                                if math.floor(current_number_of_taskmanagers * (1 - scaling_factor)) >= min_replicas:
                                        new_number_of_taskmanagers = math.floor(
                                                current_number_of_taskmanagers * (1 - scaling_factor))
                                else:
                                        new_number_of_taskmanagers = min_replicas

                                print("scaling down to: " + str(new_number_of_taskmanagers))
                                # adjusting number of taskmanagers
                        if current_number_of_taskmanagers != new_number_of_taskmanagers:
                                try:
                                        body = {"spec": {"replicas": new_number_of_taskmanagers}}
                                        api_response = v1.patch_namespaced_deployment_scale(
                                                name="flink-taskmanager", namespace="default", body=body,
                                                pretty=True)
                                        time.sleep(60)
                                except Exception as e:
                                        print(e)
                        else:
                                print("not scaling due to no change")
                else:
                        print("in cooldown period")
                time.sleep(sleep_time)


def keep_running():
        try:
                run()
        except:
                traceback.print_exc()
                time.sleep(10)
                keep_running()

keep_running()