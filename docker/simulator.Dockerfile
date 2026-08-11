FROM astrasim/tutorial-micro2024@sha256:567a0f020f18de9b7391d620041d2fd10120d6e0f1485b5110e0db79c2849305

COPY docker/simulator-requirements.txt /tmp/simulator-requirements.txt
RUN pip3 install --no-cache-dir -r /tmp/simulator-requirements.txt

COPY docker/simulator-build-requirements.txt /tmp/simulator-build-requirements.txt
RUN pip3 install --no-cache-dir -r /tmp/simulator-build-requirements.txt

# Install Chakra from the exact source nested under the pinned simulator commit.
# --no-deps intentionally retains protobuf 7.35.1, matching its generated code.
COPY third_party/LLMServingSim/astra-sim/extern/graph_frontend/chakra /tmp/chakra
RUN pip3 install --no-cache-dir --no-deps /tmp/chakra \
    && python -c "from chakra.schema.protobuf import et_def_pb2" \
    && rm -rf /tmp/chakra

WORKDIR /app/LLMServingSim
CMD ["bash"]
