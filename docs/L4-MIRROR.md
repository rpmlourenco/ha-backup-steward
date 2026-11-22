# L4 — Espelho direto para o HA2

L4 envia as origens escolhidas por rsync sobre SSH para a app **Data Steward Mirror**.
É um espelho unidirecional: ficheiros novos, modificados e eliminados na origem
refletem-se no destino. Não há Restic, VPN, armazenamento cloud intermédio ou
montagem de partilhas WAN. Os ficheiros regulares mantêm a estrutura original.

## Preparação manual

1. Instala a app Mirror no HA2 e prepara um disco ext4 dedicado, montado diretamente
   em `/media`. O recetor não monta/desmonta discos nem liga tomadas.
2. Em Alicante, abre **Configurar espelho**. Mantém **Ativar L4** desligado enquanto
   preparas o destino. Escolhe as origens existentes, inicialmente Immich e
   FileBrowser. O código aceita qualquer origem já configurada, sem exceções por nome.
3. Usa **Detetar UUID** ou confirma o UUID do filesystem de cada origem. Guarda.
4. Prepara as chaves públicas e importa o JSON de emparelhamento na app Mirror.
   Cada origem recebe uma chave Ed25519 e uma conta remota separadas.
5. Na app Mirror, guarda o disco/UUID/porta e exporta a identidade do recetor.
   Importa-a no Steward de Alicante: preenche a chave SSH, a porta e o UUID remoto.
   Preenche o endereço DNS/IP, sem `https://` nem caminho.
6. Publica manualmente a porta interna 2222/TCP da app Mirror no HA2 e configura
   router/firewall. Ativa o recetor e reinicia manualmente apenas essa app.
7. Usa **Testar ligação** no emissor: confirma autenticação, identidade, UUID e
   espaço livre do recetor, sem criar ou apagar ficheiros de dados. O painel mostra
   «A testar ligação» e depois «Ligação confirmada» ou «Teste falhou», com data e
   detalhe. Este resultado permanece visível mesmo com L4 desligado e após recarregar.
8. Ativa **L4** e, em modo de produção, escolhe **Sincronizar agora**. O diálogo
   confirma explicitamente que as eliminações também serão espelhadas.

O HA2 está inicialmente em Alicante: usa o IP LAN para acelerar a primeira carga.
Depois de mudar para Lisboa, preserva o disco/chaves e atualiza o endereço/porta.
O domínio fornecido foi `home2.rpmloureco.uk`; a resolução/ortografia não ficou
confirmada. Não foi gravado como destino e nenhuma configuração de rede foi alterada.
O reverse proxy HTTP que dá acesso ao HA2 não encaminha SSH: precisa de TCP próprio.

## Interruptores e agenda

- **Ativar L4:** permite ou bloqueia todos os novos envios. Desativar cancela e
  aguarda o processo de transferência; o que já chegou ao destino fica preservado.
- **Sincronizar diariamente:** agenda L4 na hora escolhida, no fuso da app.
  Ligar/reagendar arma a próxima ocorrência futura, sem envio imediato por atraso.
- A agenda pertence à app; não cria automações HA. Uma data diária é tentada uma
  vez; o dia perdido não cria uma fila histórica. Podes repetir manualmente.
- L4 partilha a exclusão mútua das operações locais. Aguarda a conclusão de L1–L3,
  restauro ou sessão manual de disco; uma recuperação local pendente bloqueia-o.
- Falhas de rede afetam o resultado L4, não as datas/resultados L1–L3. O espelho
  não mantém HAL3 ligado e não interfere no seu fecho seguro.
- Observação/teste nunca enviam dados para o espelho de produção. O teste explícito
  de ligação é apenas uma sonda de leitura autenticada.

## Proteções e rapidez

Antes do envio, a app valida paths/UUID ext4 das origens, bloqueia árvores vazias
e quedas excessivas relativamente à última cópia L4 concluída. Uma passagem dry-run
conta eliminações e volume a substituir antes de escrever nessa origem. O limite
de eliminações é também imposto no rsync real. O espaço livre é confirmado antes
e depois; os ficheiros eliminados são processados no fim da sincronização.

O rsync compara tamanho/data e usa transferência delta para ficheiros modificados.
SSH e rsync não comprimem fotos/vídeos por defeito. As origens são enviadas em série
para evitar concorrência de leitura/escrita no hardware HA. A velocidade apresentada
é a do progresso rsync, não uma medição independente do débito WAN. Não foi medido
o desempenho nos equipamentos ou ligações reais. Não há promessa de saturar a rede.

Não há escrita `--inplace`: os ficheiros são substituídos individualmente depois
de transferidos, com parciais confinados para permitir aproveitamento posterior.
O conjunto completo não é uma transação: uma interrupção pode deixar uma árvore
parcialmente atualizada. Repete o envio para reconciliar.

As chaves privadas ficam em `/data/steward/l4-ssh`, com permissões restritas e
incluídas no backup privado da app. O browser recebe apenas chaves públicas. A
identidade de host é fixada, sem descoberta/aceitação automática. Uma troca do
disco ou chave exige atualizar explicitamente a configuração.

O recetor usa `rrsync -wo -munge`; links simbólicos são neutralizados e ficheiros
especiais/ACL/proprietários de SO não são o objetivo deste espelho de dados.

## Consistência das aplicações

L4 espelha diretórios. Não pára Immich/FileBrowser nem cria dumps automaticamente.
Prepara previamente dumps consistentes de BD e configuração, dentro de origens
geridas, antes da janela de envio. A BD PostgreSQL do Immich pode estar fora de
`/media/immich`; uma cópia das fotografias, por si só, não recupera os metadados.
O FileBrowser também pode guardar BD/configuração na área privada da sua app.

## Validação

Os testes de unidade usam apenas ficheiros temporários e APIs falsas. As interfaces
têm testes de browser com endpoints simulados, desktop/mobile, toggle L4, chaves
públicas, confirmação e bloqueio em falha de ligação. `tests/ui_l4.py` guarda as
pré-visualizações em `data/l4-previews/`, ignorado no Git.

O repositório Mirror inclui `tests/e2e.py`, com SSH/rsync reais num contentor efémero,
sem dados/dispositivos dos HA; a identidade de montagem é simulada. A instalação,
visibilidade dos UUID no HAOS e o restauro de uma amostra exigem aceitação manual.
