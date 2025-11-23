"""
DETECTOR DE POSTURA SENTADA + SOMNOLENCIA (PRO++)
Sistema integrado para detectar:
1. Cuando una persona se sienta
2. Cuando esa persona se queda dormida

Usa MediaPipe Pose + Face Mesh
"""

from dataclasses import dataclass, field
from typing import List, Tuple, Optional, Dict, Any
from pathlib import Path
from datetime import timedelta
import csv
import statistics
import logging
import math
from collections import deque

import cv2
import numpy as np
import mediapipe as mp
import matplotlib.pyplot as plt
from IPython.display import Video, display


# ============================================================================
# CONFIGURACIÓN
# ============================================================================

@dataclass
class SleepDetectorConfig:
    """Configuración del detector de somnolencia."""

    # EAR (Eye Aspect Ratio)
    ear_threshold: float = 0.21
    ear_consec_frames_microsleep: int = 45  # ~1.5s @ 30fps = microsueño
    ear_consec_frames_sleep: int = 150      # ~5s @ 30fps = dormido

    # PERCLOS (Percentage of Eye Closure)
    perclos_window_seconds: float = 60.0
    perclos_alert_threshold: float = 0.20   # 20% del tiempo
    perclos_sleep_threshold: float = 0.40   # 40% del tiempo

    # Frecuencia de parpadeo (parpadeos por minuto)
    blink_rate_low: float = 10.0   # Muy bajo = sueño
    blink_rate_high: float = 30.0  # Muy alto = fatiga
    blink_window_seconds: float = 60.0

    # Head Pose (inclinación de cabeza)
    head_pitch_threshold: float = 25.0  # Grados hacia abajo
    head_roll_threshold: float = 15.0   # Grados lateral
    head_tilt_duration_seconds: float = 3.0

    # MAR (Mouth Aspect Ratio) - Bostezos
    mar_threshold: float = 0.6
    mar_duration_seconds: float = 2.0

    # Score combinado
    drowsiness_alert_score: float = 0.5   # Alerta
    drowsiness_sleep_score: float = 0.75  # Dormido


@dataclass
class IntegratedConfig:
    """Configuración integrada del sistema completo."""

    # Del detector de sentado (simplificado)
    target_width: int = 720
    ema_alpha_height: float = 0.2
    ema_alpha_angle: float = 0.2
    torso_max_deg: float = 30.0
    knee_sit_min: float = 75.0
    knee_sit_max: float = 120.0
    enter_hip_drop: float = 0.62
    exit_hip_drop: float = 0.55
    enter_min_seconds: float = 0.35
    exit_min_seconds: float = 0.35
    baseline_warmup_seconds: float = 1.0
    fallback_window_seconds: float = 2.0

    # Visualización
    ekg_width: int = 300
    ekg_height: int = 24
    skeleton_thickness: int = 2
    text_thickness: int = 2

    # MediaPipe
    mp_pose_complexity: int = 1
    mp_pose_detection_conf: float = 0.5
    mp_pose_tracking_conf: float = 0.5
    mp_face_detection_conf: float = 0.5
    mp_face_tracking_conf: float = 0.5

    # Sleep detector
    sleep_config: SleepDetectorConfig = field(default_factory=SleepDetectorConfig)

    # Outputs
    generate_per_frame_csv: bool = True
    generate_plot: bool = True
    plot_dpi: int = 150


@dataclass
class SleepMetrics:
    """Métricas de somnolencia por frame."""
    time_s: float
    ear_left: float
    ear_right: float
    ear_avg: float
    perclos: float
    blink_rate: float
    head_pitch: float
    head_roll: float
    mar: float
    drowsiness_score: float
    sleep_state: str  # AWAKE, DROWSY, MICROSLEEP, ASLEEP
    alert_triggered: bool


@dataclass
class CombinedFrameMetrics:
    """Métricas combinadas: postura + somnolencia."""
    time_s: float

    # Postura
    sitting_state: str
    sitting_score: float
    knee_deg: float
    torso_deg: float
    hip_drop_ratio: float

    # Somnolencia (None si no está sentado)
    sleep_metrics: Optional[SleepMetrics] = None

    # Alertas
    alert_type: Optional[str] = None  # None, DROWSY, MICROSLEEP, ASLEEP


# ============================================================================
# UTILIDADES GEOMÉTRICAS
# ============================================================================

class GeometryUtils:
    """Utilidades geométricas para pose y face."""

    @staticmethod
    def euclidean_distance(p1: Tuple[float, float], p2: Tuple[float, float]) -> float:
        """Distancia euclidiana entre dos puntos 2D."""
        return math.sqrt((p1[0] - p2[0])**2 + (p1[1] - p2[1])**2)

    @staticmethod
    def compute_ear(landmarks, w: int, h: int, eye_indices: List[int]) -> Tuple[float, List[Tuple[float, float]]]:
        """
        Calcula Eye Aspect Ratio (EAR).

        eye_indices: [outer_corner, inner_corner, top, bottom]

        EAR = vertical_dist / horizontal_dist
        """
        pts = [(landmarks[i].x * w, landmarks[i].y * h) for i in eye_indices]

        vertical = GeometryUtils.euclidean_distance(pts[2], pts[3])
        horizontal = GeometryUtils.euclidean_distance(pts[0], pts[1]) + 1e-6

        ear = vertical / horizontal
        return ear, pts

    @staticmethod
    def compute_mar(landmarks, w: int, h: int, mouth_indices: List[int]) -> float:
        """
        Calcula Mouth Aspect Ratio (MAR) para detectar bostezos.

        mouth_indices: [left, right, top, bottom]

        MAR = vertical_dist / horizontal_dist
        """
        pts = [(landmarks[i].x * w, landmarks[i].y * h) for i in mouth_indices]

        vertical = GeometryUtils.euclidean_distance(pts[2], pts[3])
        horizontal = GeometryUtils.euclidean_distance(pts[0], pts[1]) + 1e-6

        mar = vertical / horizontal
        return mar

    @staticmethod
    def compute_head_pose(landmarks, w: int, h: int) -> Tuple[float, float, float]:
        """
        Estima pose de la cabeza (pitch, yaw, roll) usando landmarks faciales.

        Returns:
            (pitch, yaw, roll) en grados
        """
        # Puntos clave para estimación de pose
        nose_tip = landmarks[1]
        chin = landmarks[152]
        left_eye_outer = landmarks[33]
        right_eye_outer = landmarks[263]
        left_mouth = landmarks[61]
        right_mouth = landmarks[291]

        # Convertir a coordenadas de imagen
        nose = np.array([nose_tip.x * w, nose_tip.y * h])
        chin_pt = np.array([chin.x * w, chin.y * h])
        left_eye = np.array([left_eye_outer.x * w, left_eye_outer.y * h])
        right_eye = np.array([right_eye_outer.x * w, right_eye_outer.y * h])

        # Pitch (arriba/abajo) - usando eje vertical cara
        vertical_vec = chin_pt - nose
        pitch = np.degrees(np.arctan2(vertical_vec[1], np.linalg.norm(vertical_vec) + 1e-6))

        # Roll (inclinación lateral) - usando eje ojos
        eye_vec = right_eye - left_eye
        roll = np.degrees(np.arctan2(eye_vec[1], eye_vec[0] + 1e-6))

        # Yaw (rotación) - aproximado
        yaw = 0.0  # Simplificado por ahora

        return pitch, yaw, roll

    @staticmethod
    def angle_between_points(a: np.ndarray, b: np.ndarray, c: np.ndarray) -> float:
        """Calcula ángulo ABC en grados (para pose)."""
        ba = a - b
        bc = c - b

        norm_ba = np.linalg.norm(ba)
        norm_bc = np.linalg.norm(bc)

        if norm_ba < 1e-6 or norm_bc < 1e-6:
            return 0.0

        cos_angle = np.dot(ba, bc) / (norm_ba * norm_bc)
        cos_angle = np.clip(cos_angle, -1.0, 1.0)

        return float(np.degrees(np.arccos(cos_angle)))

    @staticmethod
    def torso_angle_from_vertical(hip_xy: np.ndarray, shoulder_xy: np.ndarray) -> float:
        """Calcula ángulo del torso respecto a vertical."""
        vector = shoulder_xy - hip_xy
        norm = np.linalg.norm(vector)

        if norm < 1e-6:
            return 0.0

        vector_normalized = vector / norm
        vertical = np.array([0.0, -1.0])

        cos_angle = np.dot(vector_normalized, vertical)
        cos_angle = np.clip(cos_angle, -1.0, 1.0)

        return float(np.degrees(np.arccos(cos_angle)))


# ============================================================================
# DETECTOR DE SOMNOLENCIA
# ============================================================================

class SleepDetector:
    """Detector de somnolencia usando Face Mesh."""

    # Landmarks de Face Mesh
    RIGHT_EYE = [33, 133, 159, 145]  # [outer, inner, top, bottom]
    LEFT_EYE = [362, 263, 386, 374]
    MOUTH = [61, 291, 13, 14]  # [left, right, top, bottom]

    def __init__(self, config: SleepDetectorConfig, fps: float):
        self.config = config
        self.fps = fps
        self.logger = logging.getLogger(__name__)

        # Historial para PERCLOS y frecuencia de parpadeo
        self.perclos_window_frames = int(config.perclos_window_seconds * fps)
        self.blink_window_frames = int(config.blink_window_seconds * fps)

        self.eye_states = deque(maxlen=self.perclos_window_frames)  # 0=open, 1=closed
        self.blink_times = deque(maxlen=100)  # Timestamps de parpadeos

        # Estado de parpadeo
        self.frames_eyes_closed = 0
        self.last_blink_frame = 0
        self.blink_count = 0

        # Estado de inclinación de cabeza
        self.frames_head_tilted = 0

        # Estado de bostezo
        self.frames_mouth_open = 0
        self.yawn_count = 0

        # Historial de métricas
        self.metrics_history: List[SleepMetrics] = []

    def process_frame(self, face_landmarks, frame_width: int, frame_height: int,
                     frame_number: int, time_s: float) -> Tuple[SleepMetrics, bool]:
        """
        Procesa un frame con Face Mesh y retorna métricas de somnolencia.

        Returns:
            (SleepMetrics, alert_triggered)
        """
        lm = face_landmarks.landmark

        # 1. Calcular EAR
        ear_left, pts_left = GeometryUtils.compute_ear(lm, frame_width, frame_height, self.LEFT_EYE)
        ear_right, pts_right = GeometryUtils.compute_ear(lm, frame_width, frame_height, self.RIGHT_EYE)
        ear_avg = (ear_left + ear_right) / 2.0

        # 2. Detectar estado de ojos (abierto/cerrado)
        eyes_closed = ear_avg < self.config.ear_threshold
        self.eye_states.append(1 if eyes_closed else 0)

        # 3. Calcular PERCLOS
        if len(self.eye_states) > 0:
            perclos = sum(self.eye_states) / len(self.eye_states)
        else:
            perclos = 0.0

        # 4. Detectar parpadeos
        if eyes_closed:
            self.frames_eyes_closed += 1
        else:
            # Ojos se abrieron
            if self.frames_eyes_closed >= self.config.ear_consec_frames_microsleep // 3:
                # Es un parpadeo válido
                self.blink_count += 1
                self.blink_times.append(time_s)
                self.last_blink_frame = frame_number
            self.frames_eyes_closed = 0

        # 5. Calcular frecuencia de parpadeo (blinks per minute)
        if len(self.blink_times) >= 2:
            time_window = time_s - self.blink_times[0]
            if time_window > 0:
                blink_rate = (len(self.blink_times) / time_window) * 60.0
            else:
                blink_rate = 0.0
        else:
            blink_rate = 0.0

        # 6. Calcular pose de cabeza
        pitch, yaw, roll = GeometryUtils.compute_head_pose(lm, frame_width, frame_height)

        head_tilted = (abs(pitch) > self.config.head_pitch_threshold or
                      abs(roll) > self.config.head_roll_threshold)

        if head_tilted:
            self.frames_head_tilted += 1
        else:
            self.frames_head_tilted = 0

        # 7. Calcular MAR (bostezos)
        mar = GeometryUtils.compute_mar(lm, frame_width, frame_height, self.MOUTH)

        mouth_open = mar > self.config.mar_threshold
        if mouth_open:
            self.frames_mouth_open += 1
            if self.frames_mouth_open >= int(self.config.mar_duration_seconds * self.fps):
                # Es un bostezo
                self.yawn_count += 1
        else:
            self.frames_mouth_open = 0

        # 8. Calcular score de somnolencia (0-1)
        drowsiness_score = self._calculate_drowsiness_score(
            ear_avg, perclos, blink_rate, pitch, roll, mar
        )

        # 9. Determinar estado de sueño
        sleep_state, alert_triggered = self._determine_sleep_state(
            drowsiness_score, eyes_closed, perclos
        )

        # 10. Crear métricas
        metrics = SleepMetrics(
            time_s=time_s,
            ear_left=ear_left,
            ear_right=ear_right,
            ear_avg=ear_avg,
            perclos=perclos,
            blink_rate=blink_rate,
            head_pitch=pitch,
            head_roll=roll,
            mar=mar,
            drowsiness_score=drowsiness_score,
            sleep_state=sleep_state,
            alert_triggered=alert_triggered
        )

        self.metrics_history.append(metrics)

        return metrics, alert_triggered

    def _calculate_drowsiness_score(self, ear: float, perclos: float,
                                    blink_rate: float, pitch: float,
                                    roll: float, mar: float) -> float:
        """
        Calcula score de somnolencia combinando múltiples factores.

        Score: 0 (despierto) - 1 (dormido)
        """
        score = 0.0

        # 1. EAR (30% del peso)
        if ear < self.config.ear_threshold:
            ear_component = 1.0 - (ear / self.config.ear_threshold)
            score += 0.30 * ear_component

        # 2. PERCLOS (30% del peso)
        perclos_component = min(perclos / self.config.perclos_sleep_threshold, 1.0)
        score += 0.30 * perclos_component

        # 3. Frecuencia de parpadeo (15% del peso)
        if blink_rate < self.config.blink_rate_low:
            blink_component = 1.0 - (blink_rate / self.config.blink_rate_low)
            score += 0.15 * blink_component
        elif blink_rate > self.config.blink_rate_high:
            blink_component = min((blink_rate - self.config.blink_rate_high) / 20.0, 1.0)
            score += 0.10 * blink_component

        # 4. Inclinación de cabeza (15% del peso)
        head_component = 0.0
        if abs(pitch) > self.config.head_pitch_threshold:
            head_component = min(abs(pitch) / 45.0, 1.0)
        if abs(roll) > self.config.head_roll_threshold:
            head_component = max(head_component, min(abs(roll) / 30.0, 1.0))
        score += 0.15 * head_component

        # 5. Bostezos/MAR (10% del peso)
        if mar > self.config.mar_threshold:
            mar_component = min((mar - self.config.mar_threshold) / 0.4, 1.0)
            score += 0.10 * mar_component

        return min(score, 1.0)

    def _determine_sleep_state(self, drowsiness_score: float,
                               eyes_closed: bool, perclos: float) -> Tuple[str, bool]:
        """
        Determina el estado de sueño y si se debe disparar alerta.

        Returns:
            (state, alert_triggered)

        Estados:
        - AWAKE: Despierto, normal
        - DROWSY: Somnoliento, alerta preventiva
        - MICROSLEEP: Microsueño detectado, alerta crítica
        - ASLEEP: Dormido, alerta máxima
        """
        alert = False

        # Microsueño: ojos cerrados por tiempo prolongado
        if self.frames_eyes_closed >= self.config.ear_consec_frames_microsleep:
            if self.frames_eyes_closed >= self.config.ear_consec_frames_sleep:
                state = "ASLEEP"
                alert = True
            else:
                state = "MICROSLEEP"
                alert = True

        # Basado en score
        elif drowsiness_score >= self.config.drowsiness_sleep_score:
            state = "ASLEEP"
            alert = True
        elif drowsiness_score >= self.config.drowsiness_alert_score:
            state = "DROWSY"
            alert = True

        # PERCLOS alto
        elif perclos >= self.config.perclos_sleep_threshold:
            state = "ASLEEP"
            alert = True
        elif perclos >= self.config.perclos_alert_threshold:
            state = "DROWSY"
            alert = True

        else:
            state = "AWAKE"
            alert = False

        return state, alert


# ============================================================================
# DETECTOR SIMPLIFICADO DE POSTURA SENTADA
# ============================================================================

class SimpleSittingDetector:
    """Versión simplificada del detector de postura sentada."""

    def __init__(self, config: IntegratedConfig, fps: float):
        self.config = config
        self.fps = fps

        # Filtros EMA simples
        self.ema_hip = None
        self.ema_knee = None
        self.ema_torso = None

        # Estado
        self.state = "STANDING"
        self.enter_timer = 0.0
        self.exit_timer = 0.0

        # Baseline
        self.hip_baseline: Optional[float] = None
        self.baseline_samples: List[float] = []

    def process_frame(self, hip_y_norm: float, knee_deg: float,
                     torso_deg: float, time_s: float) -> Tuple[str, float]:
        """Procesa frame de pose y retorna (estado, score)."""

        # EMA
        alpha_h = self.config.ema_alpha_height
        alpha_a = self.config.ema_alpha_angle

        self.ema_hip = hip_y_norm if self.ema_hip is None else (1-alpha_h)*self.ema_hip + alpha_h*hip_y_norm
        self.ema_knee = knee_deg if self.ema_knee is None else (1-alpha_a)*self.ema_knee + alpha_a*knee_deg
        self.ema_torso = torso_deg if self.ema_torso is None else (1-alpha_a)*self.ema_torso + alpha_a*torso_deg

        # Baseline
        if self.ema_knee > 150 and abs(self.ema_torso) < 15:
            self.baseline_samples.append(self.ema_hip)
            if len(self.baseline_samples) >= int(self.config.baseline_warmup_seconds * self.fps):
                if self.hip_baseline is None:
                    self.hip_baseline = statistics.median(self.baseline_samples)

        baseline = self.hip_baseline if self.hip_baseline is not None else self.ema_hip
        hip_drop_ratio = self.ema_hip / max(baseline, 1e-6)

        # Lógica de estado
        enter_cond = (hip_drop_ratio >= self.config.enter_hip_drop and
                     self.config.knee_sit_min <= self.ema_knee <= self.config.knee_sit_max and
                     abs(self.ema_torso) <= self.config.torso_max_deg)

        exit_cond = (hip_drop_ratio <= self.config.exit_hip_drop or
                    self.ema_knee > 130 or
                    abs(self.ema_torso) > (self.config.torso_max_deg + 10))

        dt = 1.0 / self.fps

        if self.state == "STANDING":
            if enter_cond:
                self.enter_timer += dt
                if self.enter_timer >= self.config.enter_min_seconds:
                    self.state = "SITTING"
                    self.enter_timer = 0.0
            else:
                self.enter_timer = 0.0
        else:  # SITTING
            if exit_cond:
                self.exit_timer += dt
                if self.exit_timer >= self.config.exit_min_seconds:
                    self.state = "STANDING"
                    self.exit_timer = 0.0
            else:
                self.exit_timer = 0.0

        # Score simple
        score = hip_drop_ratio if self.state == "SITTING" else 0.0

        return self.state, score


print("✅ Clases de detección de somnolencia cargadas")
print("   - SleepDetector: Detección con Face Mesh")
print("   - SimpleSittingDetector: Detección de postura sentada")
print("   - GeometryUtils: Cálculos EAR, MAR, Head Pose")
