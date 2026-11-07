// 検出の確信度のしきい値の既定値。図面単位の交差検証で F1 が最も高かった値（DD-0007、schemas.py と同じ）。
export const DEFAULT_DETECTION_CONF = 0.5
// ER 系 30 枚で、コネクタの見落としを増やさず誤検出を 2 件減らした値（DD-0007 §4.20）。
export const DEFAULT_CONNECTOR_CONF = 0.6
