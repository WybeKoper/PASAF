import io
import sys

def writeConfig(**kwargs):
    template = """apiVersion: batch/v1
kind: Job
metadata:
  name: flink-jobmanager
spec:
  template:
    metadata:
      annotations:
        prometheus.io/port: '9249'
        ververica.com/scrape_every_2s: 'true'
      labels:
        app: flink
        component: jobmanager
    spec:
      restartPolicy: OnFailure
      containers:
        - name: jobmanager
          image: {container}
          imagePullPolicy: Always
          env:
          args: {args}
          ports:
            - containerPort: 6123
              name: rpc
            - containerPort: 6124
              name: blob-server
            - containerPort: 8081
              name: webui
          livenessProbe:
            tcpSocket:
              port: 6123
            initialDelaySeconds: 30
            periodSeconds: 60
          volumeMounts:
            - name: flink-config-volume
              mountPath: /opt/flink/conf
            - name: savepoint-volume
              mountPath: /opt/flink/savepoints
          securityContext:
            runAsUser: 0  # refers to user _flink_ from official flink image, change if necessary
      volumes:
        - name: flink-config-volume
          configMap:
            name: flink-config
            items:
              - key: flink-conf.yaml
                path: flink-conf.yaml
              - key: log4j-console.properties
                path: log4j-console.properties
        - name: savepoint-volume
          hostPath:
            # directory location on host
            path: /host_mnt/c/temp/soccerdb-pv
            type: DirectoryOrCreate """

    with open('jobmanager_from_savepoint.yaml', 'w') as yfile:
        yfile.write(template.format(**kwargs))


writeConfig(container="9923/demo_custom_par:2",args =["standalone-job", "--job-classname", "org.apache.flink.DemoJob", "--topic", "topic", "--bootstrap.servers",
       "kafka-service:9092", "--group.id", "yolo", "--p1", "1", "--p2", "2", "--p3", "1"])
