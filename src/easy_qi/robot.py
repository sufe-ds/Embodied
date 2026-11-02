import importlib.util
import logging
from pathlib import Path

import qi

logger = logging.getLogger(__name__)

REMOTE_AUDIO_DIR = "/tmp/embodied-movements"


class Robot:
    def __init__(self, ip, port=9559):
        self.ip = ip
        self.session = qi.Session()
        self.session.connect(f"tcp://{ip}:{port}")
        self.motion = self.session.service("ALMotion")
        self.posture = self.session.service("ALRobotPosture")
        self.behaviors = self.session.service("ALBehaviorManager")
        self.tts = self.session.service("ALTextToSpeech")
        self.audio_player = self.session.service("ALAudioPlayer")

        self.session.service("ALAutonomousLife").setState("disabled")

    def play_movement(self, name):
        path = Path("movements") / f"{name}.py"
        spec = importlib.util.spec_from_file_location(f"movement_{name}", path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Could not load movement '{name}' from {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        start_posture, _, raw = module.main()
        names = [joint[0] for joint in raw]
        times = [[float(keyframe[0]) for keyframe in joint[1]] for joint in raw]
        angles = [
            [[float(keyframe[1]), keyframe[2], keyframe[3]] for keyframe in joint[1]]
            for joint in raw
        ]

        self.motion.wakeUp()
        if start_posture:
            self.posture.goToPosture(start_posture, 0.5)

        self.motion.angleInterpolationBezier(names, times, angles)

    def play_behavior(self, name):
        self.motion.wakeUp()
        self.behaviors.runBehavior(name)

    def stop_all(self):
        for service, method in [
            (self.motion, "killAll"),
            (self.tts, "stopAll"),
            (self.behaviors, "stopAllBehaviors"),
            (self.audio_player, "stopAll"),
        ]:
            getattr(service, method)()
