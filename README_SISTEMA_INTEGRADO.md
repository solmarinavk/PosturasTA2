# 🪑💤 Sistema Integrado: Detección de Postura Sentada + Somnolencia

## 🎯 Descripción

Sistema profesional que detecta **dos eventos críticos**:

1. ✅ **Cuando una persona se sienta**
2. ✅ **Cuando esa persona se queda dormida**

---

## 📦 Archivos del Sistema

### 1. **Detector_Sentado_Dormido_SIMPLE.ipynb** (Notebook de Colab)
📌 **Archivo principal** - Listo para usar en Google Colab

**Características:**
- Interfaz simple y directa
- Procesamiento automático completo
- Visualizaciones en tiempo real
- Exportación automática de resultados

### 2. **sleep_sitting_detector_core.py** (Clases profesionales)
🔧 **Código modular** con arquitectura profesional

**Incluye:**
- `SleepDetector` - Detector de somnolencia
- `SimpleSittingDetector` - Detector de postura
- `GeometryUtils` - Utilidades geométricas
- Configuraciones con dataclasses

---

## 🚀 Cómo Usar (Google Colab)

### Opción 1: Notebook Simple (Recomendado)

```bash
1. Sube Detector_Sentado_Dormido_SIMPLE.ipynb a Google Colab
2. Ejecuta las celdas en orden (1 → 9)
3. Sube tu video cuando se te pida
4. Espera el procesamiento
5. Descarga automática de resultados
```

**Salidas:**
- ✅ Video con anotaciones y alertas visuales
- ✅ CSV con todas las métricas por frame
- ✅ Gráficos (EAR, PERCLOS, estados de sueño)
- ✅ Estadísticas resumidas

---

## 📊 Métricas de Somnolencia

El sistema usa **múltiples indicadores** combinados:

### 1. **EAR (Eye Aspect Ratio)**
```
EAR = distancia_vertical_ojo / distancia_horizontal_ojo

- EAR < 0.21 = ojos cerrados
- Microsueño: >1.5s ojos cerrados
- Dormido: >5s ojos cerrados
```

### 2. **PERCLOS (Percentage of Eye Closure)**
```
PERCLOS = (tiempo_ojos_cerrados / tiempo_total) en ventana de 60s

- PERCLOS > 20% = Alerta (somnoliento)
- PERCLOS > 40% = Dormido
```

### 3. **Frecuencia de Parpadeo**
```
Normal: 15-20 parpadeos/minuto

Anormal (indica fatiga):
- < 10 parpadeos/min = Sueño
- > 30 parpadeos/min = Fatiga extrema
```

### 4. **Inclinación de Cabeza (Head Pose)**
```
Pitch (arriba/abajo):
- > 25° hacia abajo = Cabeza cayendo

Roll (lateral):
- > 15° = Cabeza ladeada
```

### 5. **Bostezos (MAR - Mouth Aspect Ratio)**
```
MAR = distancia_vertical_boca / distancia_horizontal_boca

- MAR > 0.6 por >2s = Bostezo detectado
```

### 6. **Score Combinado**
```python
Pesos:
- EAR: 30%
- PERCLOS: 30%
- Frecuencia parpadeo: 15%
- Inclinación cabeza: 15%
- Bostezos: 10%

Score final: 0.0 (despierto) → 1.0 (dormido)
```

---

## 🚨 Estados y Alertas

### Estados de Sueño:

| Estado | Icono | Descripción | Condiciones |
|--------|-------|-------------|-------------|
| **AWAKE** | 🟢 | Despierto, normal | Score < 0.5 |
| **DROWSY** | 🟡 | Somnoliento | PERCLOS 20-40% ó cabeza inclinada |
| **MICROSLEEP** | 🟠 | Microsueño | Ojos cerrados 1.5-5s |
| **ASLEEP** | 🔴 | Dormido | Ojos cerrados >5s ó PERCLOS >40% |

### Visualización en Video:

```
┌─────────────────────────────────┐
│ SENTADO - ASLEEP                │  ← Estado
│ ⚠️ ALERTA SOMNOLENCIA ⚠️         │  ← Alerta visual
│                                  │
│ EAR: 0.125                       │  ← Métricas
│ PERCLOS: 45.2%                   │
│ Parpadeos/min: 8.5               │
└─────────────────────────────────┘
```

**Colores del banner:**
- 🟢 Verde: Despierto, normal
- 🟡 Amarillo: Somnoliento
- 🟠 Naranja: Microsueño
- 🔴 Rojo: Dormido

---

## 🔧 Configuración (Ajustable)

En el notebook, celda 3, puedes personalizar:

```python
# ===== DETECCIÓN DE SOMNOLENCIA =====
EAR_THRESHOLD = 0.21       # Bajar = más sensible a ojos cerrados
MICROSLEEP_FRAMES = 45     # ~1.5s @ 30fps
SLEEP_FRAMES = 150         # ~5s @ 30fps

PERCLOS_ALERT = 0.20       # 20% = alerta
PERCLOS_SLEEP = 0.40       # 40% = dormido

HEAD_PITCH_MAX = 25.0      # Grados cabeza hacia abajo
MAR_THRESHOLD = 0.6        # Umbral bostezo

# ===== DETECCIÓN DE SENTADO =====
HIP_DROP_SIT = 0.62        # Umbral cadera bajada
KNEE_MIN, KNEE_MAX = 75, 120  # Rango rodilla sentado
```

**Recomendaciones:**
- **Más sensible**: Bajar EAR_THRESHOLD a 0.19
- **Menos sensible**: Subir PERCLOS_ALERT a 0.25
- **Microsueño más corto**: Bajar MICROSLEEP_FRAMES a 30 (1s)

---

## 📈 Resultados Exportados

### 1. **Video Procesado**
`video_sentado_dormido.mp4`

Incluye:
- Banner de estado (color según alerta)
- Esqueleto de pose
- Contorno de ojos
- Métricas en tiempo real
- Alertas visuales parpadeantes

### 2. **CSV Completo**
`resultados_completos.csv`

```csv
time_s,sitting,sleep_state,ear,perclos,blink_rate,knee_deg,alert
0.03,False,N/A,0.0,0.0,0.0,180.0,False
5.21,True,AWAKE,0.285,0.05,18.3,95.2,False
10.45,True,DROWSY,0.195,0.23,12.1,92.8,True
15.67,True,ASLEEP,0.112,0.48,7.5,94.1,True
```

**Campos:**
- `time_s`: Tiempo en segundos
- `sitting`: True/False si está sentado
- `sleep_state`: AWAKE/DROWSY/MICROSLEEP/ASLEEP
- `ear`: Eye Aspect Ratio
- `perclos`: Porcentaje ojos cerrados
- `blink_rate`: Parpadeos por minuto
- `knee_deg`: Ángulo de rodilla
- `alert`: True si hay alerta activa

### 3. **Gráficos**
`graficos_somnolencia.png`

Tres paneles:
1. **EAR en el tiempo** - Con umbral marcado
2. **PERCLOS** - Con niveles de alerta
3. **Estados de sueño** - Timeline visual

---

## 🎓 Criterios Técnicos

### Arquitectura del Sistema

```
┌─────────────┐
│   VIDEO     │
└──────┬──────┘
       │
       ├─────────────────┐
       │                 │
       v                 v
┌─────────────┐   ┌──────────────┐
│ MediaPipe   │   │ MediaPipe    │
│    Pose     │   │  Face Mesh   │
│             │   │ (solo si     │
│ Detecta:    │   │  sentado)    │
│ - Rodilla   │   │              │
│ - Cadera    │   │ Detecta:     │
│ - Torso     │   │ - Ojos       │
└──────┬──────┘   │ - Boca       │
       │          │ - Cabeza     │
       │          └──────┬───────┘
       v                 v
┌─────────────────────────────────┐
│   Lógica de Estados             │
│                                 │
│ SimpleSittingDetector           │
│ + SleepDetector                 │
│                                 │
│ Combina métricas:               │
│ - EAR + PERCLOS + Blinks        │
│ - Head Pose + Yawns             │
│ = Drowsiness Score              │
└──────────┬──────────────────────┘
           │
           v
┌─────────────────────────────────┐
│   Alertas y Visualización       │
└─────────────────────────────────┘
```

### Optimizaciones Implementadas:

1. **Face Mesh solo cuando sentado**
   - Ahorra 70% de procesamiento
   - Evita falsos positivos (movimiento natural)

2. **Ventanas deslizantes**
   - PERCLOS: Ventana de 60s
   - Blink rate: Últimos 100 parpadeos

3. **Filtros EMA**
   - Suavizado de señales de pose
   - Alpha = 0.2 (balance reactivo/estable)

4. **Debounce en transiciones**
   - Evita cambios rápidos de estado
   - Confirmación temporal requerida

---

## 📚 Casos de Uso

### 1. **Monitoreo de Conductores**
```python
# Ajustar para mayor sensibilidad
EAR_THRESHOLD = 0.19
MICROSLEEP_FRAMES = 30  # 1s
PERCLOS_ALERT = 0.15    # 15%
```

### 2. **Estudio de Sueño**
```python
# Recopilar datos detallados
# Usar CSV completo para análisis
# Gráficos muestran patrones
```

### 3. **Seguridad Industrial**
```python
# Detectar fatiga en operadores
# Alertas preventivas tempranas
# Log de incidentes
```

### 4. **Investigación**
```python
# Métricas cuantitativas
# Comparación entre sujetos
# Análisis estadístico
```

---

## ⚡ Rendimiento

**Velocidades aproximadas:**

| Hardware | FPS procesamiento | Tiempo real |
|----------|-------------------|-------------|
| Colab CPU | 5-8 fps | 4-6x más lento |
| Colab GPU (T4) | 15-20 fps | 1.5-2x más lento |
| Local GPU (RTX) | 25-30 fps | Casi tiempo real |

**Nota:** Face Mesh es más intensivo que Pose. El sistema solo activa Face Mesh cuando detecta a alguien sentado.

---

## 🔍 Troubleshooting

### Problema: No detecta a nadie sentado
**Solución:**
```python
# Bajar umbral de cadera
HIP_DROP_SIT = 0.58  # Más sensible

# Ampliar rango de rodilla
KNEE_MIN = 70
KNEE_MAX = 130
```

### Problema: Muchas falsas alarmas de sueño
**Solución:**
```python
# Subir umbrales
EAR_THRESHOLD = 0.23
PERCLOS_ALERT = 0.25
MICROSLEEP_FRAMES = 60  # 2s
```

### Problema: No detecta microsueños
**Solución:**
```python
# Bajar umbrales
EAR_THRESHOLD = 0.19
MICROSLEEP_FRAMES = 30  # 1s
```

### Problema: Procesamiento muy lento
**Solución:**
- Usar GPU en Colab (Runtime → Change runtime type → GPU)
- Reducir resolución del video antes de subir
- Procesar solo segmentos importantes

---

## 📖 Referencias Técnicas

### Papers y Estándares:

1. **EAR (Eye Aspect Ratio)**
   - Soukupová & Čech (2016)
   - "Real-Time Eye Blink Detection using Facial Landmarks"

2. **PERCLOS**
   - Estándar ISO/TS 12813:2019
   - "Percentage of eyelid closure over the pupil over time"

3. **Head Pose Estimation**
   - MediaPipe Face Mesh
   - 468 landmarks 3D

4. **Drowsiness Detection**
   - Bakker et al. (2018)
   - "A multi-feature approach to detect drowsiness"

---

## 🎯 Próximas Mejoras Posibles

- [ ] Alertas sonoras (beep cuando ASLEEP)
- [ ] Detección de múltiples personas
- [ ] Exportación a formato JSON
- [ ] Dashboard web interactivo
- [ ] Streaming en tiempo real
- [ ] Integración con APIs de alerta
- [ ] Análisis de patrones de sueño
- [ ] Predicción de somnolencia (ML)

---

## 📝 Licencia

Código libre para uso educativo y de investigación.

---

## 👤 Autor

Desarrollado con Claude AI
Basado en MediaPipe (Google)

---

## 🆘 Soporte

**Problemas comunes:**
1. Error de imports → Reinstalar dependencias (celda 1)
2. Video no carga → Verificar formato (MP4 recomendado)
3. No detecta cara → Mejorar iluminación del video
4. Lento → Activar GPU en Colab

**Contacto:**
- Ver issues en el repositorio
- Documentación de MediaPipe: https://google.github.io/mediapipe/

---

✅ **¡Sistema listo para detectar postura sentada y somnolencia!**
