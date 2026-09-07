"""
Ajuda a descobrir o índice correto da câmera (webcam do notebook,
celular via Iriun, etc.) reconhecida pelo Windows.

Uso: python list_cameras.py
Mostra uma janela por câmera encontrada (índices 0 a 4). Anota o
índice da janela que mostrar a imagem certa e feche as outras.
Pressione qualquer tecla em cada janela para passar pra próxima.
"""
import cv2

for index in range(5):
    cap = cv2.VideoCapture(index)
    if not cap.isOpened():
        print(f"Índice {index}: nenhuma câmera encontrada.")
        cap.release()
        continue

    success, frame = cap.read()
    if success:
        print(f"Índice {index}: câmera encontrada! Mostrando por 3 segundos...")
        cv2.imshow(f"Camera index {index} - pressione qualquer tecla", frame)
        cv2.waitKey(3000)
        cv2.destroyAllWindows()
    else:
        print(f"Índice {index}: abriu mas não conseguiu ler frame.")

    cap.release()

print("\nUse o índice que mostrou a imagem certa no seu programV3_live_example.py:")
print('cap = cv2.VideoCapture(<indice_aqui>)')
