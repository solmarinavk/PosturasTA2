"""
PERSONA SENTADA (PRO++ Visual) — MediaPipe Pose + Señales + Métricas
Versión profesional optimizada para Google Colab

Mejoras implementadas:
- Arquitectura modular con clases y funciones
- Type hints completos
- Dataclasses para configuración
- Optimización de memoria y rendimiento
- Mejor manejo de errores y logging
- Visualizaciones mejoradas
- Métricas avanzadas
"""

from dataclasses import dataclass, field
from typing import List, Tuple, Optional, Dict, Any
from pathlib import Path
from datetime import timedelta
from contextlib import contextmanager
import csv
import statistics
import logging

import cv2
import numpy as np
import mediapipe as mp
import matplotlib.pyplot as plt
from IPython.display import Video, display


# ============================================================================
# CONFIGURACIÓN
# ============================================================================

@dataclass
class DetectorConfig:
    """Configuración centralizada del detector de postura sentada."""

    # Procesamiento de video
    target_width: int = 720
    video_quality: int = 23  # CRF para H.264 (0-51, menor = mejor calidad)

    # Suavizado EMA
    ema_alpha_height: float = 0.2
    ema_alpha_angle: float = 0.2

    # Umbrales anatómicos
    torso_max_deg: float = 30.0
    knee_sit_min: float = 75.0
    knee_sit_max: float = 120.0

    # Umbrales de detección (con histéresis)
    enter_hip_drop: float = 0.62
    exit_hip_drop: float = 0.55
    enter_min_seconds: float = 0.35
    exit_min_seconds: float = 0.35

    # Calibración baseline
    baseline_warmup_seconds: float = 1.0
    fallback_window_seconds: float = 2.0

    # Visualización
    ekg_width: int = 300
    ekg_height: int = 24
    skeleton_thickness: int = 2
    text_thickness: int = 2

    # MediaPipe
    mp_model_complexity: int = 1
    mp_min_detection_confidence: float = 0.5
    mp_min_tracking_confidence: float = 0.5

    # Outputs
    generate_per_frame_csv: bool = True
    generate_plot: bool = True
    plot_dpi: int = 150


@dataclass
class FrameMetrics:
    """Métricas calculadas por frame."""
    time_s: float
    hip_y_norm: float
    knee_deg: float
    torso_deg: float
    hip_drop_ratio: float
    sitting_score: float
    state: str
    visibility: float


@dataclass
class SessionResults:
    """Resultados de una sesión completa de análisis."""
    total_duration_s: float
    sitting_duration_s: float
    sitting_ratio: float
    num_transitions: int
    avg_episode_duration_s: float
    intervals: List[Tuple[float, float]]
    frame_metrics: List[FrameMetrics] = field(default_factory=list)

    def summary_dict(self) -> Dict[str, Any]:
        """Retorna resumen como diccionario."""
        return {
            'total_duration_s': round(self.total_duration_s, 2),
            'sitting_duration_s': round(self.sitting_duration_s, 2),
            'sitting_ratio': round(self.sitting_ratio, 3),
            'num_transitions': self.num_transitions,
            'avg_episode_duration_s': round(self.avg_episode_duration_s, 2),
            'num_frames': len(self.frame_metrics)
        }


# ============================================================================
# UTILIDADES GEOMÉTRICAS
# ============================================================================

class GeometryUtils:
    """Utilidades para cálculos geométricos de pose."""

    @staticmethod
    def angle_between_points(a: np.ndarray, b: np.ndarray, c: np.ndarray) -> float:
        """
        Calcula el ángulo ABC en grados.

        Args:
            a, b, c: Puntos 2D como arrays numpy

        Returns:
            Ángulo en grados (0-180)
        """
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
        """
        Calcula el ángulo del torso respecto a la vertical.

        Args:
            hip_xy: Posición de la cadera en 2D
            shoulder_xy: Posición del hombro en 2D

        Returns:
            Ángulo respecto a la vertical en grados (0° = perfectamente vertical)
        """
        vector = shoulder_xy - hip_xy
        norm = np.linalg.norm(vector)

        if norm < 1e-6:
            return 0.0

        vector_normalized = vector / norm
        vertical = np.array([0.0, -1.0])  # Hacia arriba en coordenadas de imagen

        cos_angle = np.dot(vector_normalized, vertical)
        cos_angle = np.clip(cos_angle, -1.0, 1.0)

        return float(np.degrees(np.arccos(cos_angle)))

    @staticmethod
    def clamp(value: float, min_val: float = 0.0, max_val: float = 1.0) -> float:
        """Limita un valor entre min y max."""
        return max(min_val, min(max_val, value))


# ============================================================================
# FILTROS Y PROCESAMIENTO DE SEÑALES
# ============================================================================

class EMAFilter:
    """Filtro de media móvil exponencial (EMA) para suavizado de señales."""

    def __init__(self, alpha: float = 0.2):
        """
        Args:
            alpha: Factor de suavizado (0-1). Mayor = más reactivo
        """
        self.alpha = alpha
        self.value: Optional[float] = None

    def update(self, new_value: float) -> float:
        """Actualiza el filtro con un nuevo valor."""
        if self.value is None:
            self.value = new_value
        else:
            self.value = (1 - self.alpha) * self.value + self.alpha * new_value
        return self.value

    def reset(self):
        """Reinicia el filtro."""
        self.value = None


# ============================================================================
# DETECTOR DE POSTURA SENTADA
# ============================================================================

class SittingDetector:
    """Detector principal de postura sentada con máquina de estados."""

    def __init__(self, config: DetectorConfig):
        self.config = config
        self.logger = logging.getLogger(__name__)

        # Filtros EMA
        self.ema_hip = EMAFilter(config.ema_alpha_height)
        self.ema_knee = EMAFilter(config.ema_alpha_angle)
        self.ema_torso = EMAFilter(config.ema_alpha_angle)

        # Estado y calibración
        self.state = "STANDING"
        self.enter_timer = 0.0
        self.exit_timer = 0.0

        self.baseline_hip_samples: List[float] = []
        self.hip_baseline: Optional[float] = None
        self.fallback_samples: List[Tuple[float, float, float]] = []

        # Resultados
        self.intervals: List[Tuple[float, float]] = []
        self.start_sit_time: Optional[float] = None
        self.frame_metrics: List[FrameMetrics] = []

    def calculate_sitting_score(self, hip_drop_ratio: float, knee_deg: float) -> float:
        """
        Calcula un score continuo de 0 a 1 que indica qué tan sentada está la persona.

        Args:
            hip_drop_ratio: Ratio de bajada de cadera (mayor = más sentado)
            knee_deg: Ángulo de rodilla en grados

        Returns:
            Score de 0 (de pie) a 1 (sentado)
        """
        # Componente de cadera
        hip_normalized = (hip_drop_ratio - self.config.enter_hip_drop) / \
                        (1.0 - self.config.enter_hip_drop)
        hip_component = GeometryUtils.clamp(hip_normalized)

        # Componente de rodilla (ideal = 90°)
        knee_deviation = abs(knee_deg - 90.0) / 90.0
        knee_component = GeometryUtils.clamp(1.0 - knee_deviation)

        # Combinar componentes
        return 0.5 * hip_component + 0.5 * knee_component

    def update_baseline(self, hip_y_norm: float, knee_deg: float,
                       torso_deg: float, time_s: float, fps: float):
        """
        Actualiza el baseline de cadera cuando la persona está claramente de pie.

        Args:
            hip_y_norm: Posición Y normalizada de la cadera
            knee_deg: Ángulo de rodilla
            torso_deg: Ángulo del torso
            time_s: Tiempo actual en segundos
            fps: Frames por segundo
        """
        # Detectar postura "de pie" clara
        is_standing = (knee_deg > 150 and abs(torso_deg) < 15)

        if is_standing:
            self.baseline_hip_samples.append(hip_y_norm)

            # Establecer baseline después del período de warmup
            min_samples = int(self.config.baseline_warmup_seconds * fps)
            if len(self.baseline_hip_samples) >= min_samples and self.hip_baseline is None:
                self.hip_baseline = statistics.median(self.baseline_hip_samples)
                self.logger.info(f"Baseline establecido: {self.hip_baseline:.4f}")

        # Recolectar muestras para fallback
        if time_s <= self.config.fallback_window_seconds:
            self.fallback_samples.append((hip_y_norm, knee_deg, time_s))

    def finalize_baseline(self):
        """Establece baseline usando fallback si nunca se detectó postura de pie."""
        if self.hip_baseline is not None:
            return

        if not self.fallback_samples:
            self.logger.warning("No hay muestras para establecer baseline")
            return

        # Usar percentil 70 de ángulos de rodilla como umbral
        knee_angles = [k for _, k, _ in self.fallback_samples if k is not None]
        if not knee_angles:
            return

        threshold = np.percentile(knee_angles, 70)
        candidates = [h for h, k, _ in self.fallback_samples
                     if k is not None and k >= threshold]

        if candidates:
            self.hip_baseline = float(np.median(candidates))
            self.logger.info(f"Baseline fallback establecido: {self.hip_baseline:.4f}")

    def process_frame(self, hip_y_norm: float, knee_deg: float,
                     torso_deg: float, time_s: float, fps: float,
                     visibility: float) -> Tuple[str, float]:
        """
        Procesa un frame y actualiza el estado del detector.

        Args:
            hip_y_norm: Posición Y normalizada de la cadera
            knee_deg: Ángulo de rodilla
            torso_deg: Ángulo del torso
            time_s: Tiempo actual en segundos
            fps: Frames por segundo
            visibility: Score de visibilidad

        Returns:
            Tupla (estado actual, sitting score)
        """
        # Aplicar filtros EMA
        ema_hip = self.ema_hip.update(hip_y_norm)
        ema_knee = self.ema_knee.update(knee_deg)
        ema_torso = self.ema_torso.update(torso_deg)

        # Actualizar baseline
        self.update_baseline(ema_hip, ema_knee, ema_torso, time_s, fps)

        # Usar baseline provisional si aún no se ha establecido
        if self.hip_baseline is None:
            current_baseline = ema_hip
        else:
            current_baseline = self.hip_baseline

        # Calcular métricas
        hip_drop_ratio = ema_hip / max(current_baseline, 1e-6)
        sitting_score = self.calculate_sitting_score(hip_drop_ratio, ema_knee)

        # Condiciones de estado con histéresis
        enter_condition = (
            hip_drop_ratio >= self.config.enter_hip_drop and
            self.config.knee_sit_min <= ema_knee <= self.config.knee_sit_max and
            abs(ema_torso) <= self.config.torso_max_deg
        )

        exit_condition = (
            hip_drop_ratio <= self.config.exit_hip_drop or
            ema_knee > 130 or
            abs(ema_torso) > (self.config.torso_max_deg + 10)
        )

        # Máquina de estados con debounce
        dt = 1.0 / fps

        if self.state == "STANDING":
            if enter_condition:
                self.enter_timer += dt
                if self.enter_timer >= self.config.enter_min_seconds:
                    self.state = "SITTING"
                    self.enter_timer = 0.0
                    self.start_sit_time = time_s
                    self.logger.debug(f"Transición STANDING -> SITTING @ {time_s:.2f}s")
            else:
                self.enter_timer = 0.0

        else:  # SITTING
            if exit_condition:
                self.exit_timer += dt
                if self.exit_timer >= self.config.exit_min_seconds:
                    self.state = "STANDING"
                    self.exit_timer = 0.0
                    if self.start_sit_time is not None:
                        self.intervals.append((self.start_sit_time, time_s))
                        self.logger.debug(f"Transición SITTING -> STANDING @ {time_s:.2f}s")
                        self.start_sit_time = None
            else:
                self.exit_timer = 0.0

        # Guardar métricas del frame
        metrics = FrameMetrics(
            time_s=time_s,
            hip_y_norm=ema_hip,
            knee_deg=ema_knee,
            torso_deg=ema_torso,
            hip_drop_ratio=hip_drop_ratio,
            sitting_score=sitting_score,
            state=self.state,
            visibility=visibility
        )
        self.frame_metrics.append(metrics)

        return self.state, sitting_score

    def finalize(self, final_time_s: float) -> SessionResults:
        """
        Finaliza la sesión y retorna resultados completos.

        Args:
            final_time_s: Tiempo final del video

        Returns:
            Objeto SessionResults con todos los resultados
        """
        # Establecer baseline si no se hizo
        self.finalize_baseline()

        # Cerrar intervalo abierto si existe
        if self.state == "SITTING" and self.start_sit_time is not None:
            self.intervals.append((self.start_sit_time, final_time_s))

        # Calcular estadísticas
        total_sitting = sum(end - start for start, end in self.intervals)
        ratio = total_sitting / final_time_s if final_time_s > 0 else 0.0
        avg_duration = total_sitting / len(self.intervals) if self.intervals else 0.0

        return SessionResults(
            total_duration_s=final_time_s,
            sitting_duration_s=total_sitting,
            sitting_ratio=ratio,
            num_transitions=len(self.intervals),
            avg_episode_duration_s=avg_duration,
            intervals=self.intervals,
            frame_metrics=self.frame_metrics
        )


# ============================================================================
# PROCESADOR DE POSE (MediaPipe)
# ============================================================================

class PoseProcessor:
    """Procesador de pose usando MediaPipe."""

    def __init__(self, config: DetectorConfig):
        self.config = config
        self.mp_pose = mp.solutions.pose
        self.mp_draw = mp.solutions.drawing_utils

        self.pose = self.mp_pose.Pose(
            static_image_mode=False,
            model_complexity=config.mp_model_complexity,
            min_detection_confidence=config.mp_min_detection_confidence,
            min_tracking_confidence=config.mp_min_tracking_confidence
        )

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.pose.close()

    def process_frame(self, frame_bgr: np.ndarray) -> Optional[Any]:
        """
        Procesa un frame y retorna los landmarks de pose.

        Args:
            frame_bgr: Frame en formato BGR

        Returns:
            Resultado de MediaPipe o None si no se detectó pose
        """
        frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        return self.pose.process(frame_rgb)

    def extract_keypoints(self, landmarks, frame_width: int, frame_height: int) \
            -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, str, float]:
        """
        Extrae puntos clave relevantes de los landmarks.

        Returns:
            Tupla (hip_px, knee_px, ankle_px, shoulder_px, side, visibility)
        """
        lm = landmarks.landmark

        # Índices de landmarks
        LSH = self.mp_pose.PoseLandmark.LEFT_SHOULDER.value
        RSH = self.mp_pose.PoseLandmark.RIGHT_SHOULDER.value
        LHIP = self.mp_pose.PoseLandmark.LEFT_HIP.value
        RHIP = self.mp_pose.PoseLandmark.RIGHT_HIP.value
        LKNEE = self.mp_pose.PoseLandmark.LEFT_KNEE.value
        RKNEE = self.mp_pose.PoseLandmark.RIGHT_KNEE.value
        LANK = self.mp_pose.PoseLandmark.LEFT_ANKLE.value
        RANK = self.mp_pose.PoseLandmark.RIGHT_ANKLE.value

        # Calcular visibilidad por lado
        left_vis = min(lm[LKNEE].visibility, lm[LANK].visibility)
        right_vis = min(lm[RKNEE].visibility, lm[RANK].visibility)

        # Seleccionar lado con mejor visibilidad
        if right_vis > left_vis:
            side = "R"
            hip_idx, knee_idx, ankle_idx, shoulder_idx = RHIP, RKNEE, RANK, RSH
            visibility = right_vis
        else:
            side = "L"
            hip_idx, knee_idx, ankle_idx, shoulder_idx = LHIP, LKNEE, LANK, LSH
            visibility = left_vis

        # Extraer coordenadas en píxeles
        hip_px = np.array([lm[hip_idx].x * frame_width, lm[hip_idx].y * frame_height])
        knee_px = np.array([lm[knee_idx].x * frame_width, lm[knee_idx].y * frame_height])
        ankle_px = np.array([lm[ankle_idx].x * frame_width, lm[ankle_idx].y * frame_height])
        shoulder_px = np.array([lm[shoulder_idx].x * frame_width, lm[shoulder_idx].y * frame_height])

        return hip_px, knee_px, ankle_px, shoulder_px, side, visibility


# ============================================================================
# VISUALIZADOR
# ============================================================================

class Visualizer:
    """Maneja todas las visualizaciones en el video."""

    def __init__(self, config: DetectorConfig, frame_width: int, frame_height: int):
        self.config = config
        self.frame_width = frame_width
        self.frame_height = frame_height

        # Barra EKG (historial temporal)
        self.ekg = np.zeros((config.ekg_height, config.ekg_width, 3), dtype=np.uint8)

    def draw_skeleton(self, frame: np.ndarray, pose_landmarks, mp_pose, mp_draw):
        """Dibuja el esqueleto sobre el frame."""
        mp_draw.draw_landmarks(
            frame, pose_landmarks, mp_pose.POSE_CONNECTIONS,
            mp_draw.DrawingSpec(color=(0, 255, 0), thickness=self.config.skeleton_thickness, circle_radius=2),
            mp_draw.DrawingSpec(color=(0, 0, 255), thickness=self.config.skeleton_thickness)
        )

    def draw_state_banner(self, frame: np.ndarray, state: str):
        """Dibuja banner semitransparente con el estado."""
        overlay = frame.copy()
        banner_color = (0, 180, 0) if state == "SITTING" else (180, 0, 0)
        cv2.rectangle(overlay, (0, 0), (self.frame_width, 68), banner_color, -1)
        frame[:] = cv2.addWeighted(overlay, 0.25, frame, 0.75, 0)

        label = "✅ Persona SENTADA" if state == "SITTING" else "🟦 Persona DE PIE"
        cv2.putText(frame, label, (22, 45), cv2.FONT_HERSHEY_SIMPLEX,
                   1.2, (255, 255, 255), 3, cv2.LINE_AA)

    def draw_metrics(self, frame: np.ndarray, knee_deg: float, torso_deg: float,
                    hip_drop: float, score: float):
        """Dibuja métricas numéricas."""
        text1 = f"knee:{knee_deg:5.1f}°  torso:{torso_deg:5.1f}°  hip_drop:{hip_drop:4.2f}"
        cv2.putText(frame, text1, (22, 90), cv2.FONT_HERSHEY_SIMPLEX,
                   0.75, (255, 255, 255), self.config.text_thickness, cv2.LINE_AA)

        text2 = f"score:{score:0.2f}"
        cv2.putText(frame, text2, (22, 120), cv2.FONT_HERSHEY_SIMPLEX,
                   0.75, (0, 255, 255), self.config.text_thickness, cv2.LINE_AA)

    def draw_hip_lines(self, frame: np.ndarray, baseline: Optional[float],
                      current: float):
        """Dibuja líneas de referencia de cadera."""
        if baseline is not None:
            baseline_y = int(baseline * self.frame_height)
            cv2.line(frame, (0, baseline_y), (self.frame_width, baseline_y),
                    (255, 255, 255), 1, cv2.LINE_AA)

        current_y = int(current * self.frame_height)
        cv2.line(frame, (0, current_y), (self.frame_width, current_y),
                (0, 255, 255), 2, cv2.LINE_AA)

    def update_ekg(self, state: str):
        """Actualiza la barra EKG."""
        self.ekg[:, :-1] = self.ekg[:, 1:]  # Shift left
        color = (0, 255, 0) if state == "SITTING" else (255, 0, 0)
        self.ekg[:, -1] = color

    def draw_ekg(self, frame: np.ndarray):
        """Dibuja la barra EKG en el frame."""
        y0 = self.frame_height - self.config.ekg_height - 8
        x0 = self.frame_width - self.config.ekg_width - 8
        frame[y0:y0 + self.config.ekg_height, x0:x0 + self.config.ekg_width] = self.ekg


# ============================================================================
# EXPORTADORES
# ============================================================================

class ResultsExporter:
    """Maneja la exportación de resultados a diferentes formatos."""

    @staticmethod
    def export_intervals_csv(intervals: List[Tuple[float, float]], filepath: Path):
        """Exporta intervalos de sesiones sentadas a CSV."""
        with open(filepath, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['start_s', 'end_s', 'duration_s'])
            for start, end in intervals:
                writer.writerow([round(start, 3), round(end, 3), round(end - start, 3)])

    @staticmethod
    def export_frame_metrics_csv(metrics: List[FrameMetrics], filepath: Path):
        """Exporta métricas por frame a CSV."""
        with open(filepath, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['time_s', 'state', 'knee_deg', 'torso_deg',
                           'hip_drop_ratio', 'sit_score', 'visibility'])
            for m in metrics:
                writer.writerow([
                    round(m.time_s, 3), m.state, round(m.knee_deg, 2),
                    round(m.torso_deg, 2), round(m.hip_drop_ratio, 3),
                    round(m.sitting_score, 3), round(m.visibility, 3)
                ])

    @staticmethod
    def create_signals_plot(metrics: List[FrameMetrics], filepath: Path, dpi: int = 150):
        """Crea gráfico de señales temporales."""
        if not metrics:
            return

        times = [m.time_s for m in metrics]
        hips = [m.hip_drop_ratio for m in metrics]
        knees = [m.knee_deg / 180.0 for m in metrics]
        torsos = [m.torso_deg / 90.0 for m in metrics]
        states = [1 if m.state == "SITTING" else 0 for m in metrics]
        scores = [m.sitting_score for m in metrics]

        fig, ax = plt.subplots(figsize=(12, 5))

        ax.plot(times, hips, label='Hip drop ratio', linewidth=2, alpha=0.8)
        ax.plot(times, knees, label='Knee (deg/180)', linewidth=1.8, alpha=0.8)
        ax.plot(times, torsos, label='Torso (deg/90)', linewidth=1.8, alpha=0.8)
        ax.plot(times, scores, label='Sit score', linewidth=2, linestyle='--', alpha=0.8)
        ax.fill_between(times, 0, states, color='green', alpha=0.15, label='Sitting state')

        ax.set_ylim(-0.1, 1.1)
        ax.set_xlabel('Tiempo (s)', fontsize=11)
        ax.set_ylabel('Escala normalizada', fontsize=11)
        ax.set_title('Señales de Postura en el Tiempo', fontsize=13, fontweight='bold')
        ax.grid(True, alpha=0.3, linestyle='--')
        ax.legend(loc='upper right', ncol=2, framealpha=0.9)

        plt.tight_layout()
        plt.savefig(filepath, dpi=dpi, bbox_inches='tight')
        plt.close(fig)


# ============================================================================
# PIPELINE PRINCIPAL
# ============================================================================

class SittingAnalysisPipeline:
    """Pipeline completo de análisis de postura sentada."""

    def __init__(self, config: DetectorConfig):
        self.config = config
        self.logger = self._setup_logger()

    def _setup_logger(self) -> logging.Logger:
        """Configura el logger."""
        logger = logging.getLogger(__name__)
        logger.setLevel(logging.INFO)

        if not logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter('%(levelname)s: %(message)s')
            handler.setFormatter(formatter)
            logger.addHandler(handler)

        return logger

    def process_video(self, input_path: Path, output_path: Path) -> SessionResults:
        """
        Procesa un video completo y genera resultados.

        Args:
            input_path: Ruta al video de entrada
            output_path: Ruta para el video de salida

        Returns:
            SessionResults con todos los resultados del análisis
        """
        self.logger.info(f"Procesando video: {input_path}")

        # Abrir video
        cap = cv2.VideoCapture(str(input_path))
        if not cap.isOpened():
            raise ValueError(f"No se pudo abrir el video: {input_path}")

        try:
            # Obtener propiedades del video
            fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
            orig_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            orig_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

            # Calcular dimensiones de salida
            scale = min(1.0, self.config.target_width / max(1, orig_width))
            out_width = int(orig_width * scale)
            out_height = int(orig_height * scale)

            self.logger.info(f"Video: {orig_width}x{orig_height} @ {fps:.1f}fps, "
                           f"{total_frames} frames")
            self.logger.info(f"Salida: {out_width}x{out_height}")

            # Crear writer de video (directo a MP4 con H.264)
            fourcc = cv2.VideoWriter_fourcc(*'mp4v')
            writer = cv2.VideoWriter(
                str(output_path), fourcc, fps, (out_width, out_height)
            )

            if not writer.isOpened():
                raise ValueError("No se pudo crear el video de salida")

            try:
                # Inicializar componentes
                detector = SittingDetector(self.config)
                visualizer = Visualizer(self.config, out_width, out_height)

                with PoseProcessor(self.config) as pose_processor:
                    frame_idx = 0

                    while True:
                        ret, frame = cap.read()
                        if not ret:
                            break

                        frame_idx += 1
                        time_s = frame_idx / fps

                        # Redimensionar si es necesario
                        if scale != 1.0:
                            frame = cv2.resize(frame, (out_width, out_height),
                                             interpolation=cv2.INTER_AREA)

                        # Procesar pose
                        result = pose_processor.process_frame(frame)

                        if result and result.pose_landmarks:
                            # Extraer puntos clave
                            hip_px, knee_px, ankle_px, shoulder_px, side, visibility = \
                                pose_processor.extract_keypoints(
                                    result.pose_landmarks, out_width, out_height
                                )

                            # Calcular ángulos
                            knee_deg = GeometryUtils.angle_between_points(hip_px, knee_px, ankle_px)
                            torso_deg = GeometryUtils.torso_angle_from_vertical(hip_px, shoulder_px)

                            # Obtener posición normalizada de cadera
                            lm = result.pose_landmarks.landmark
                            if side == "R":
                                hip_idx = pose_processor.mp_pose.PoseLandmark.RIGHT_HIP.value
                            else:
                                hip_idx = pose_processor.mp_pose.PoseLandmark.LEFT_HIP.value
                            hip_y_norm = lm[hip_idx].y

                            # Procesar con detector
                            state, score = detector.process_frame(
                                hip_y_norm, knee_deg, torso_deg, time_s, fps, visibility
                            )

                            # Obtener métricas actuales
                            current_metrics = detector.frame_metrics[-1]

                            # Dibujar visualizaciones
                            visualizer.draw_skeleton(
                                frame, result.pose_landmarks,
                                pose_processor.mp_pose, pose_processor.mp_draw
                            )
                            visualizer.draw_state_banner(frame, state)
                            visualizer.draw_metrics(
                                frame, knee_deg, torso_deg,
                                current_metrics.hip_drop_ratio, score
                            )
                            visualizer.draw_hip_lines(
                                frame, detector.hip_baseline, hip_y_norm
                            )
                            visualizer.update_ekg(state)
                            visualizer.draw_ekg(frame)

                        # Escribir frame
                        writer.write(frame)

                        # Log de progreso
                        if frame_idx % 100 == 0:
                            progress = (frame_idx / total_frames) * 100
                            self.logger.info(f"Progreso: {progress:.1f}% ({frame_idx}/{total_frames})")

                # Finalizar análisis
                final_time = frame_idx / fps
                results = detector.finalize(final_time)

                self.logger.info("Procesamiento completado")
                return results

            finally:
                writer.release()

        finally:
            cap.release()

    def print_summary(self, results: SessionResults):
        """Imprime resumen de resultados."""
        print("\n" + "="*60)
        print(" RESUMEN DE ANÁLISIS ".center(60, "="))
        print("="*60)
        print(f"  Duración total:        {results.total_duration_s:.2f}s "
              f"(~{timedelta(seconds=int(results.total_duration_s))})")
        print(f"  Tiempo sentado:        {results.sitting_duration_s:.2f}s "
              f"({results.sitting_ratio*100:.1f}%)")
        print(f"  Transiciones:          {results.num_transitions}")
        print(f"  Promedio por episodio: {results.avg_episode_duration_s:.2f}s")
        print(f"  Frames analizados:     {len(results.frame_metrics)}")
        print("="*60 + "\n")


# ============================================================================
# FUNCIÓN PRINCIPAL PARA COLAB
# ============================================================================

def run_sitting_detection_colab():
    """
    Función principal para ejecutar en Google Colab.
    Incluye toda la lógica de upload, procesamiento y visualización.
    """
    from google.colab import files

    # Configuración
    config = DetectorConfig()

    # Subir video
    print("📌 Sube tu video (MP4 recomendado):")
    uploaded = files.upload()

    if not uploaded:
        print("❌ No se subió ningún video")
        return

    input_path = Path(list(uploaded.keys())[0])
    output_path = Path("sentado_visual_pro.mp4")

    # Crear pipeline y procesar
    pipeline = SittingAnalysisPipeline(config)

    try:
        results = pipeline.process_video(input_path, output_path)

        # Descargar automáticamente el video procesado
        print("\n📥 Descargando video procesado...")
        files.download(str(output_path))
        print("✅ Descarga completada")

        # Exportar resultados
        print("\n📊 Exportando resultados...")

        # CSV de intervalos
        csv_intervals = Path("sentado_intervalos.csv")
        ResultsExporter.export_intervals_csv(results.intervals, csv_intervals)
        print(f"  ✓ CSV intervalos: {csv_intervals}")

        # CSV por frame
        if config.generate_per_frame_csv:
            csv_frames = Path("sentado_per_frame.csv")
            ResultsExporter.export_frame_metrics_csv(results.frame_metrics, csv_frames)
            print(f"  ✓ CSV frames: {csv_frames}")

        # Gráfico
        if config.generate_plot:
            plot_path = Path("senales_postura.png")
            ResultsExporter.create_signals_plot(results.frame_metrics, plot_path, config.plot_dpi)
            print(f"  ✓ Gráfico: {plot_path}")

        # Mostrar resumen
        pipeline.print_summary(results)

        # Mostrar video inline (método más confiable en Colab)
        print("📹 Reproduciendo video procesado:")
        print("   (El video también fue descargado automáticamente)\n")
        display(Video(str(output_path), width=720, embed=True))
        print("\n💡 Consejo: Si el video no se reproduce inline, ya fue descargado a tu computadora.")

        # Mostrar gráfico
        if config.generate_plot and plot_path.exists():
            print("\n📈 Gráfico de señales:")
            plt.figure(figsize=(12, 5))
            img = plt.imread(plot_path)
            plt.imshow(img)
            plt.axis('off')
            plt.tight_layout()
            plt.show()

        print("\n✅ Procesamiento completado con éxito")

    except Exception as e:
        print(f"\n❌ Error durante el procesamiento: {e}")
        raise


# ============================================================================
# EJECUCIÓN
# ============================================================================

if __name__ == "__main__":
    # Esta sección se ejecuta automáticamente en Colab
    run_sitting_detection_colab()
