# GestaoEstacionamento
Sistema de monitoramento de vagas de estacionamento baseado em visão computacional, com suporte a câmeras 360° e configuração adaptável dos ambientes por meio da definição manual das áreas de estacionamento.

## Avaliação experimental

A tela **Testes** permite executar um ambiente em diferentes vídeos/fontes. O mesmo conjunto de vagas pode ser avaliado contra outra gravação, e também é possível selecionar um vídeo diretamente pelo navegador.

Após o processamento, cada amostra é salva em `test_results/<ambiente>_<id>/samples/` e os resultados automáticos ficam em `data.csv`. O teste também salva `metadata.json` com a configuração utilizada.

### Gabarito manual

Na tela de revisão, cada amostra apresenta a imagem original e o estado detectado pelo YOLO para cada vaga. O operador marca manualmente **Ocupada** ou **Livre**. As marcações ficam em `ground_truth.json` e podem ser corrigidas posteriormente.

### Métricas

A partir do gabarito são calculados:

- TP, FP, TN e FN;
- acurácia;
- precisão;
- recall;
- taxa de falsos positivos;
- quantidade de acertos e erros.

As métricas são calculadas por vaga e no conjunto completo e ficam disponíveis em `metrics.json` e `metrics.csv`.

### Movimento da câmera/vídeo

A estabilização é opcional por teste. O sistema usa os pontos de referência da imagem salva do ambiente para estimar uma transformação entre a referência e o frame atual. A implementação usa homografia com RANSAC e possui fallback para transformação afim parcial quando a homografia não é confiável.

A estabilização deve ser entendida como compensação de movimento da imagem. Ela **não é correção de distorção fisheye**. Em deslocamentos grandes, mudanças fortes de perspectiva ou panoramas 360° com distorção e mudança de projeção, a correspondência pode deixar de ser confiável; o resultado registra essa condição em `tracking_estavel` e `match_count` para permitir que o comportamento seja analisado nos testes.

## Tela de testes do TCC

A tela `/tests` é uma ferramenta independente para os experimentos e avaliações do TCC. Ela não faz parte do fluxo do usuário final e não é aberta automaticamente junto com o sistema.

Para utilizá-la, inicie normalmente o Smart Parking e depois execute `ABRIR_TESTES.bat` ou acesse diretamente `http://127.0.0.1:5000/tests`.

O dashboard e o editor de configuração não possuem links para a tela de testes. Os testes servem exclusivamente para analisar vídeos, comparar as detecções com um gabarito manual e obter métricas para a avaliação acadêmica.
