.PHONY: kind-up install run demo test clean

kind-up:          ## create a local kind cluster
	kind create cluster --name greenlight || true

install:          ## install the CRD + RBAC
	kubectl apply -f deploy/crd.yaml
	kubectl apply -f deploy/rbac.yaml

run:              ## run the controller locally (simulate mode)
	GREENLIGHT_SIMULATE=true kopf run -m greenlight.controller --all-namespaces

demo:             ## apply the sample rollout and watch it
	kubectl apply -f examples/modelrollout-sample.yaml
	kubectl get modelrollout demo -w

test:             ## run unit tests
	python -m pytest -q

clean:
	kubectl delete -f examples/modelrollout-sample.yaml --ignore-not-found
	kind delete cluster --name greenlight || true
