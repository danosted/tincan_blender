// Builds the Unity-side assets of com.tincan.characters from the imported
// SK_KidBase.fbx: material, avatar mask, animator controller, KidBase prefab
// (a variant of the model) and the CharacterLab sample scene.
//
// Lives in this repo (scripts/unity/), not in the package: it's authoring
// tooling. scripts/cli/character_lab.py copies it into the CharacterLab
// project's Assets/Editor/ and runs it headless:
//     Unity -batchmode -quit -projectPath ~/CharacterLab -executeMethod TinCan.CharacterLab.KidAssetBuilder.BuildAll
//
// Re-running keeps every asset's GUID: existing assets are updated in place,
// never deleted and recreated, so prefabs and scenes in the game that point
// at them keep working across versions.
using System.IO;
using System.Linq;
using TinCan.Characters;
using UnityEditor;
using UnityEditor.Animations;
using UnityEditor.SceneManagement;
using UnityEngine;

namespace TinCan.CharacterLab
{
    public static class KidAssetBuilder
    {
        const string Pkg = "Packages/com.tincan.characters";
        const string ModelPath = Pkg + "/Runtime/Models/SK_KidBase.fbx";
        const string ShaderName = "TinCan/Toon";
        const string MaterialPath = Pkg + "/Runtime/Materials/M_Kid_Toon.mat";
        const string MaskPath = Pkg + "/Runtime/Animations/AM_Kid_UpperBody.mask";
        const string ControllerPath = Pkg + "/Runtime/Animations/AC_Kid.controller";
        const string PrefabPath = Pkg + "/Runtime/Prefabs/KidBase.prefab";
        // The sample is edited as a normal Assets/ folder (Unity can't see
        // Samples~) and copied back into the package by character_lab.py.
        const string SampleDir = "Assets/Samples/CharacterLab";
        const string ScenePath = SampleDir + "/LabScene.unity";

        // Must match build_kid.py WALK_SPEED / RUN_SPEED.
        const float WalkSpeed = 1.3f, RunSpeed = 3.4f;

        [MenuItem("TinCan/Build Kid Assets")]
        public static void BuildAll()
        {
            AssetDatabase.ImportAsset(ModelPath, ImportAssetOptions.ForceUpdate);
            var model = AssetDatabase.LoadAssetAtPath<GameObject>(ModelPath);
            Require(model != null, $"model not found at {ModelPath} - run build_kid.py");
            var avatar = AssetDatabase.LoadAllAssetsAtPath(ModelPath).OfType<Avatar>().FirstOrDefault();
            Require(avatar != null && avatar.isHuman, "SK_KidBase avatar missing or not Humanoid");
            var clips = AssetDatabase.LoadAllAssetsAtPath(ModelPath).OfType<AnimationClip>()
                .Where(c => !c.name.StartsWith("__preview__")).ToDictionary(c => c.name);
            foreach (var n in new[] { "A_Kid_Idle", "A_Kid_Walk", "A_Kid_Run", "A_Kid_Grab" })
                Require(clips.ContainsKey(n), $"clip {n} missing; have [{string.Join(", ", clips.Keys)}]");

            var material = BuildMaterial();
            var mask = BuildMask();
            var controller = BuildController(clips, mask);
            BuildPrefab(model, avatar, controller, material);
            AssetDatabase.SaveAssets();
            BuildLabScene();
            AssetDatabase.SaveAssets();
            Debug.Log("[KidBuilder] OK");
        }

        static void Require(bool ok, string message)
        {
            if (!ok)
                throw new System.InvalidOperationException("[KidBuilder] " + message);
        }

        static Material BuildMaterial()
        {
            var shader = Shader.Find(ShaderName);
            Require(shader != null, $"shader {ShaderName} not found");
            var mat = AssetDatabase.LoadAssetAtPath<Material>(MaterialPath);
            if (mat == null)
            {
                mat = new Material(shader);
                AssetDatabase.CreateAsset(mat, MaterialPath);
            }
            mat.shader = shader;
            mat.enableInstancing = true;
            EditorUtility.SetDirty(mat);
            return mat;
        }

        static AvatarMask BuildMask()
        {
            var mask = AssetDatabase.LoadAssetAtPath<AvatarMask>(MaskPath);
            if (mask == null)
            {
                mask = new AvatarMask();
                AssetDatabase.CreateAsset(mask, MaskPath);
            }
            for (var part = AvatarMaskBodyPart.Root; part < AvatarMaskBodyPart.LastBodyPart; part++)
            {
                bool upper = part == AvatarMaskBodyPart.Body || part == AvatarMaskBodyPart.Head ||
                             part == AvatarMaskBodyPart.LeftArm || part == AvatarMaskBodyPart.RightArm ||
                             part == AvatarMaskBodyPart.LeftFingers || part == AvatarMaskBodyPart.RightFingers ||
                             part == AvatarMaskBodyPart.LeftHandIK || part == AvatarMaskBodyPart.RightHandIK;
                mask.SetHumanoidBodyPartActive(part, upper);
            }
            mask.transformCount = 0;
            EditorUtility.SetDirty(mask);
            return mask;
        }

        static AnimatorController BuildController(System.Collections.Generic.Dictionary<string, AnimationClip> clips, AvatarMask mask)
        {
            // Build fresh in a temp file, then copy its YAML over the package's
            // controller so the package asset keeps its GUID.
            const string tmp = "Assets/__AC_Kid_tmp.controller";
            var ac = AnimatorController.CreateAnimatorControllerAtPath(tmp);
            ac.AddParameter("Speed", AnimatorControllerParameterType.Float);

            var baseLayer = ac.layers[0];
            baseLayer.name = "Locomotion";
            var sm = baseLayer.stateMachine;
            var locomotion = ac.CreateBlendTreeInController("Locomotion", out var tree, 0);
            tree.blendType = BlendTreeType.Simple1D;
            tree.blendParameter = "Speed";
            tree.useAutomaticThresholds = false;
            tree.AddChild(clips["A_Kid_Idle"], 0f);
            tree.AddChild(clips["A_Kid_Walk"], WalkSpeed);
            tree.AddChild(clips["A_Kid_Run"], RunSpeed);
            sm.defaultState = locomotion;
            ac.layers = new[] { baseLayer };

            var grabSm = new AnimatorStateMachine { name = "Grab", hideFlags = HideFlags.HideInHierarchy };
            AssetDatabase.AddObjectToAsset(grabSm, ac);
            var grabState = grabSm.AddState("Grab");
            grabState.motion = clips["A_Kid_Grab"];
            grabSm.defaultState = grabState;
            ac.AddLayer(new AnimatorControllerLayer
            {
                name = CharacterView.GrabLayerName,
                stateMachine = grabSm,
                avatarMask = mask,
                blendingMode = AnimatorLayerBlendingMode.Override,
                defaultWeight = 0f,
                iKPass = true, // CharacterView.OnAnimatorIK lands the hands here
            });
            AssetDatabase.SaveAssets();

            string full(string p) => Path.GetFullPath(p);
            if (AssetDatabase.LoadAssetAtPath<AnimatorController>(ControllerPath) == null)
            {
                AssetDatabase.MoveAsset(tmp, ControllerPath);
            }
            else
            {
                File.Copy(full(tmp), full(ControllerPath), true);
                AssetDatabase.DeleteAsset(tmp);
                AssetDatabase.ImportAsset(ControllerPath, ImportAssetOptions.ForceUpdate);
            }
            return AssetDatabase.LoadAssetAtPath<AnimatorController>(ControllerPath);
        }

        static void BuildPrefab(GameObject model, Avatar avatar, AnimatorController controller, Material material)
        {
            var go = (GameObject)PrefabUtility.InstantiatePrefab(model);
            try
            {
                go.name = "KidBase";
                var animator = go.GetComponent<Animator>() ?? go.AddComponent<Animator>();
                animator.avatar = avatar;
                animator.runtimeAnimatorController = controller;
                animator.applyRootMotion = false;
                animator.cullingMode = AnimatorCullingMode.CullUpdateTransforms;

                foreach (var r in go.GetComponentsInChildren<Renderer>(true))
                    r.sharedMaterials = Enumerable.Repeat(material, r.sharedMaterials.Length).ToArray();

                Transform Bone(HumanBodyBones b) => animator.GetBoneTransform(b);
                Transform Find(string n) => go.GetComponentsInChildren<Transform>(true).First(t => t.name == n);

                var spring = go.GetComponent<SpringBoneChain>() ?? go.AddComponent<SpringBoneChain>();
                spring.Configure(
                    Enumerable.Range(0, 4).Select(i => Find($"Scarf_{i}")).ToArray(),
                    new[]
                    {
                        new SpringBoneChain.SphereCollider { center = Bone(HumanBodyBones.Chest), offset = Vector3.zero, radius = 0.1f },
                        new SpringBoneChain.SphereCollider { center = Bone(HumanBodyBones.Spine), offset = Vector3.zero, radius = 0.095f },
                        new SpringBoneChain.SphereCollider { center = Bone(HumanBodyBones.Hips), offset = Vector3.zero, radius = 0.1f },
                    });

                if (go.GetComponent<CharacterView>() == null) go.AddComponent<CharacterView>();
                if (go.GetComponent<PlayerColor>() == null) go.AddComponent<PlayerColor>();
                var appearance = go.GetComponent<CharacterAppearance>() ?? go.AddComponent<CharacterAppearance>();
                if (!appearance.Slots.Any(s => s.name == "Scarf"))
                    appearance.AddSlot(new CharacterAppearance.Slot
                    {
                        name = "Scarf",
                        worn = true,
                        objects = new[] { Find("Scarf").gameObject },
                        behaviours = new Behaviour[] { spring },
                    });

                PrefabUtility.SaveAsPrefabAsset(go, PrefabPath, out bool ok);
                Require(ok, "saving KidBase.prefab failed");
            }
            finally
            {
                Object.DestroyImmediate(go);
            }
        }

        static void BuildLabScene()
        {
            Directory.CreateDirectory(SampleDir);
            var scene = EditorSceneManager.NewScene(NewSceneSetup.DefaultGameObjects, NewSceneMode.Single);
            var prefab = AssetDatabase.LoadAssetAtPath<GameObject>(PrefabPath);

            var floor = GameObject.CreatePrimitive(PrimitiveType.Plane);
            floor.name = "Deck";
            floor.transform.localScale = new Vector3(2, 1, 2);

            var player = (GameObject)PrefabUtility.InstantiatePrefab(prefab);
            player.name = "Player (WASD, Shift, E, C, X)";
            var cc = player.AddComponent<CharacterController>();
            cc.height = 1.3f;
            cc.radius = 0.2f;
            cc.center = new Vector3(0, 0.66f, 0);
            cc.stepOffset = 0.25f;
            var labPlayerType = System.Type.GetType("TinCan.Characters.Samples.LabPlayer, TinCan.Characters.Samples.CharacterLab");
            Require(labPlayerType != null, "LabPlayer not compiled - is the CharacterLab sample copied into Assets/Samples?");
            player.AddComponent(labPlayerType);

            var crew = (GameObject)PrefabUtility.InstantiatePrefab(prefab);
            crew.name = "Crewmate (idle)";
            crew.transform.SetPositionAndRotation(new Vector3(1.2f, 0, 0.8f), Quaternion.Euler(0, -150, 0));
            crew.GetComponent<PlayerColor>().Color = new Color(0.95f, 0.55f, 0.15f);

            BuildHelm(new Vector3(0, 0, 2.4f));

            var cam = Camera.main;
            cam.transform.position = new Vector3(0, 1.6f, -3.2f);
            cam.transform.LookAt(new Vector3(0, 0.8f, 0));
            var labCamType = System.Type.GetType("TinCan.Characters.Samples.LabCamera, TinCan.Characters.Samples.CharacterLab");
            var labCam = cam.gameObject.AddComponent(labCamType);
            labCamType.GetMethod("SetTarget").Invoke(labCam, new object[] { player.transform });

            EditorSceneManager.SaveScene(scene, ScenePath);
        }

        // A dummy helm whose handles match the Grab pose (see KidTestRig).
        static void BuildHelm(Vector3 standPoint)
        {
            var root = new GameObject("Helm (StationGrip)");
            root.transform.position = standPoint;
            var post = GameObject.CreatePrimitive(PrimitiveType.Cylinder);
            post.name = "Post";
            post.transform.SetParent(root.transform, false);
            post.transform.localPosition = new Vector3(0, 0.42f, 0.55f);
            post.transform.localScale = new Vector3(0.08f, 0.42f, 0.08f);
            var wheel = GameObject.CreatePrimitive(PrimitiveType.Cylinder);
            wheel.name = "Wheel";
            wheel.transform.SetParent(root.transform, false);
            wheel.transform.localPosition = new Vector3(0, 0.86f, 0.47f);
            wheel.transform.localRotation = Quaternion.Euler(90, 0, 0);
            wheel.transform.localScale = new Vector3(0.36f, 0.02f, 0.36f);
            Transform Target(string n, Vector3 p)
            {
                var t = new GameObject(n).transform;
                t.SetParent(root.transform, false);
                t.localPosition = p;
                return t;
            }
            var grip = root.AddComponent<StationGrip>();
            grip.SetTargets(Target("LeftHandle", new Vector3(-0.15f, 0.86f, 0.40f)), Target("RightHandle", new Vector3(0.15f, 0.86f, 0.40f)));
        }
    }
}
